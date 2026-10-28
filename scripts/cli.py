#!/usr/bin/env python3
"""Operator CLI for SentinelIQ: seed, list, export, import, verify, backup, restore, tick.

Examples:
    python scripts/cli.py seed --count 6
    python scripts/cli.py list --priority P1 --state open
    python scripts/cli.py export --format csv --output incidents.csv
    python scripts/cli.py verify-data
    python scripts/cli.py backup
    python scripts/cli.py restore backups/json-20261029T101500Z.json --yes
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bootstrap import build_context  # noqa: E402
from errors import ApiError, ConfigError  # noqa: E402
from repository import (  # noqa: E402
    ENVELOPE_VERSION,
    VALID_CATEGORIES,
    VALID_CRITICALITIES,
    VALID_PRIORITIES,
    VALID_STATES,
    StorageError,
)
from seed import seed_demo  # noqa: E402
from util import file_lock, utc_now  # noqa: E402

BACKUP_DIR = PROJECT_ROOT / "backups"


def _timestamp() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def _print_json(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _context(args):
    return build_context()


def _tenant(args, context) -> str:
    return args.tenant or context.settings.default_tenant


# -- commands -----------------------------------------------------------
def cmd_seed(args) -> int:
    context = _context(args)
    tenant = _tenant(args, context)
    created = seed_demo(context.service, tenant, args.count)
    if args.json:
        _print_json(
            {
                "tenant": tenant,
                "seeded": len(created),
                "incidents": [{"id": item["id"], "service": item["service"], "priority": item["priority"]} for item in created],
            }
        )
    else:
        print(f"Seeded {len(created)} incidents into tenant '{tenant}'.")
    return 0


def cmd_list(args) -> int:
    context = _context(args)
    tenant = _tenant(args, context)
    filters = {
        "priority": args.priority,
        "state": args.state,
        "category": args.category,
        "owner": args.owner,
        "environment": args.environment,
        "search": args.search,
        "sort": args.sort,
        "order": args.order,
        "limit": args.limit,
        "offset": args.offset,
    }
    records, total = context.service.list(tenant, **{key: value for key, value in filters.items() if value is not None})
    if args.json:
        _print_json({"tenant": tenant, "total": total, "count": len(records), "incidents": records})
        return 0
    if not records:
        print(f"No incidents matched in tenant '{tenant}' (total {total}).")
        return 0
    header = ("PRIORITY", "SCORE", "STATE", "SERVICE", "OWNER", "ENV", "CREATED", "ID")
    rows = [
        (
            str(item.get("priority", "")),
            f"{float(item.get('score_100') or 0):.1f}",
            str(item.get("state", "")),
            str(item.get("service", "")),
            str(item.get("owner", "")),
            str(item.get("environment", "")),
            str(item.get("created_at", ""))[:19],
            str(item.get("id", "")),
        )
        for item in records
    ]
    widths = [max(len(str(row[index])) for row in (header, *rows)) for index in range(len(header))]
    line = "  ".join(str(value).ljust(widths[index]) for index, value in enumerate(header))
    print(line)
    print("-" * len(line))
    for row in rows:
        print("  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))
    print(f"\n{len(records)} of {total} incident(s) in tenant '{tenant}'.")
    return 0


def cmd_export(args) -> int:
    context = _context(args)
    tenant = _tenant(args, context)
    exported = context.service.export(tenant, args.format)
    target = Path(args.output) if args.output else Path(exported["filename"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(exported["body"])
    print(f"Exported {tenant} to {target} ({len(exported['body'])} bytes, {exported['content_type']}).")
    return 0


def cmd_import(args) -> int:
    context = _context(args)
    tenant = _tenant(args, context)
    source = Path(args.input)
    if not source.exists():
        raise ConfigError(f"import file not found: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"import file is not valid JSON: {exc}") from exc
    result = context.service.import_records(tenant, payload)
    print(f"Imported {result['imported']} incident(s) into tenant '{tenant}'.")
    return 0


def _parse_iso(value: str) -> bool:
    try:
        from datetime import datetime

        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (TypeError, ValueError):
        return False


def cmd_verify_data(args) -> int:
    context = _context(args)
    tenant = _tenant(args, context)
    repository = context.repository
    problems: list[str] = []
    if not repository.ping():
        problems.append("storage ping failed")
    records = repository.all_incidents(tenant)
    seen: dict[str, int] = {}
    for index, record in enumerate(records):
        incident_id = str(record.get("id") or "")
        if not incident_id:
            problems.append(f"record[{index}] has no id")
        else:
            seen[incident_id] = seen.get(incident_id, 0) + 1
        if not record.get("service"):
            problems.append(f"record[{index}] ({incident_id or '?'}) has no service")
        for field, valid in (
            ("state", VALID_STATES),
            ("priority", VALID_PRIORITIES),
            ("category", VALID_CATEGORIES),
            ("criticality", VALID_CRITICALITIES),
        ):
            if record.get(field) not in valid:
                problems.append(f"{incident_id or f'record[{index}]'}: invalid {field} {record.get(field)!r}")
        for field in ("created_at", "updated_at"):
            value = record.get(field)
            if not value:
                problems.append(f"{incident_id or f'record[{index}]'}: missing {field}")
            elif not _parse_iso(str(value)):
                problems.append(f"{incident_id or f'record[{index}]'}: {field} is not ISO-8601 ({value!r})")
        if not isinstance(record.get("events"), list):
            problems.append(f"{incident_id or f'record[{index}]'}: events must be a list")
        try:
            if not 0 <= float(record.get("score_100") or 0) <= 100:
                problems.append(f"{incident_id or f'record[{index}]'}: score_100 out of range")
        except (TypeError, ValueError):
            problems.append(f"{incident_id or f'record[{index}]'}: score_100 is not numeric")
    duplicates = sorted(key for key, count in seen.items() if count > 1)
    if duplicates:
        problems.append(f"duplicate ids: {', '.join(duplicates)}")

    report = {
        "tenant": tenant,
        "backend": context.settings.storage_backend,
        "incidents": len(records),
        "duplicate_ids": duplicates,
        "problems": problems,
        "ok": not problems,
    }
    if args.json:
        _print_json(report)
    else:
        print(f"Backend: {report['backend']}  Tenant: {tenant}  Incidents: {len(records)}")
        if problems:
            print(f"Found {len(problems)} problem(s):")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print("Data verification passed: no problems found.")
    return 0 if not problems else 1


def cmd_summary(args) -> int:
    context = _context(args)
    tenant = _tenant(args, context)
    summary = context.service.summary(tenant)
    if args.json:
        _print_json(summary)
        return 0
    print(f"Tenant: {tenant}")
    print(f"Total incidents : {summary['total_incidents']}")
    print(f"Open            : {summary['open_incidents']}")
    print(f"By priority     : {summary['by_priority']}")
    print(f"MTTA            : {_seconds(summary.get('mtta_seconds'))}")
    print(f"MTTR            : {_seconds(summary.get('mttr_seconds'))}")
    print(f"Oldest open age : {_seconds(summary.get('oldest_open_age_seconds'))}")
    return 0


def _seconds(value) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.0f}s"


def cmd_tick(args) -> int:
    context = _context(args)
    result = context.service.tick()
    if args.json:
        _print_json(result)
    else:
        print(
            f"Outbox tick: processed={result['processed']} sent={result['sent']} "
            f"failed={result['failed']} dead={result['dead']}"
        )
    return 0


def cmd_backup(args) -> int:
    context = _context(args)
    settings = context.settings
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if args.output:
        target = Path(args.output)
    else:
        extension = {"json": "json", "sqlite": "db", "postgres": "json"}[settings.storage_backend]
        target = BACKUP_DIR / f"{settings.storage_backend}-{_timestamp()}.{extension}"
    target.parent.mkdir(parents=True, exist_ok=True)

    if settings.storage_backend == "json":
        with file_lock(settings.data_file.with_name(settings.data_file.name + ".lock")):
            shutil.copy2(settings.data_file, target)
    elif settings.storage_backend == "sqlite":
        source = sqlite3.connect(settings.sqlite_path)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
    else:
        exported = context.service.export(settings.default_tenant, "json")
        target.write_bytes(exported["body"])

    size = target.stat().st_size
    print(f"Backup written to {target} ({size} bytes, backend {settings.storage_backend}).")
    return 0


def _validate_json_backup(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"backup is not valid JSON: {exc}") from exc
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict) or not isinstance(payload.get("incidents"), list):
        raise ConfigError("JSON backup must contain an incidents list")
    version = int(payload.get("version", ENVELOPE_VERSION))
    if version > ENVELOPE_VERSION:
        raise ConfigError(f"backup envelope version {version} is newer than supported {ENVELOPE_VERSION}")
    return len(payload["incidents"])


def cmd_restore(args) -> int:
    context = _context(args)
    settings = context.settings
    source = Path(args.input)
    if not source.exists():
        raise ConfigError(f"backup file not found: {source}")

    if not args.yes:
        raise ConfigError("restore overwrites current data; re-run with --yes to confirm")

    if settings.storage_backend == "json":
        count = _validate_json_backup(source)
        pre_restore = settings.data_file.with_name(f"{settings.data_file.name}.pre-restore-{_timestamp()}")
        if settings.data_file.exists():
            shutil.copy2(settings.data_file, pre_restore)
        with file_lock(settings.data_file.with_name(settings.data_file.name + ".lock")):
            shutil.copy2(source, settings.data_file)
        print(f"Restored {count} incident(s) from {source}. Previous data saved to {pre_restore}.")
    elif settings.storage_backend == "sqlite":
        probe = sqlite3.connect(source)
        try:
            count = probe.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise ConfigError(f"backup is not a valid SentinelIQ SQLite database: {exc}") from exc
        finally:
            probe.close()
        pre_restore = settings.sqlite_path.with_name(f"{settings.sqlite_path.name}.pre-restore-{_timestamp()}")
        context.repository.close()
        if settings.sqlite_path.exists():
            shutil.copy2(settings.sqlite_path, pre_restore)
        shutil.copy2(source, settings.sqlite_path)
        print(f"Restored SQLite database with {count} incident(s). Previous database saved to {pre_restore}.")
    else:
        count = _validate_json_backup(source)
        payload = json.loads(source.read_text(encoding="utf-8"))
        records = payload if isinstance(payload, list) else payload.get("incidents", [])
        result = context.service.import_records(settings.default_tenant, records)
        print(f"Restored {result['imported']} of {count} incident(s) into Postgres (upsert semantics).")
    return 0


# -- argument parsing ---------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SentinelIQ operations CLI")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--tenant", help="tenant to operate on (default: SENTINELIQ_DEFAULT_TENANT)")
    common.add_argument("--json", action="store_true", help="emit machine-readable JSON where supported")
    subparsers = parser.add_subparsers(dest="command", required=True)

    seed = subparsers.add_parser("seed", parents=[common], help="load realistic demo incidents")
    seed.add_argument("--count", type=int, default=6, help="number of incidents (1-6)")
    seed.set_defaults(func=cmd_seed)

    listing = subparsers.add_parser("list", parents=[common], help="list incidents")
    listing.add_argument("--priority", choices=sorted(VALID_PRIORITIES))
    listing.add_argument("--state", choices=sorted(VALID_STATES))
    listing.add_argument("--category", choices=sorted(VALID_CATEGORIES))
    listing.add_argument("--owner")
    listing.add_argument("--environment")
    listing.add_argument("--search")
    listing.add_argument("--sort", default="created_at")
    listing.add_argument("--order", choices=["asc", "desc"], default="desc")
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--offset", type=int, default=0)
    listing.set_defaults(func=cmd_list)

    export = subparsers.add_parser("export", parents=[common], help="export incidents to JSON or CSV")
    export.add_argument("--format", choices=["json", "csv"], default="json")
    export.add_argument("--output", help="target file (default: generated filename)")
    export.set_defaults(func=cmd_export)

    importer = subparsers.add_parser("import", parents=[common], help="import incidents from a JSON export")
    importer.add_argument("--input", required=True)
    importer.set_defaults(func=cmd_import)

    verify = subparsers.add_parser("verify-data", parents=[common], help="check stored data for corruption")
    verify.set_defaults(func=cmd_verify_data)

    summary = subparsers.add_parser("summary", parents=[common], help="print MTTA/MTTR and priority summary")
    summary.set_defaults(func=cmd_summary)

    tick = subparsers.add_parser("tick", parents=[common], help="process the notification outbox once")
    tick.set_defaults(func=cmd_tick)

    backup = subparsers.add_parser("backup", parents=[common], help="take a consistent backup of the data store")
    backup.add_argument("--output", help="target file (default: backups/<backend>-<timestamp>)")
    backup.set_defaults(func=cmd_backup)

    restore = subparsers.add_parser("restore", parents=[common], help="restore data from a backup")
    restore.add_argument("--input", required=True)
    restore.add_argument("--yes", action="store_true", help="confirm overwriting current data")
    restore.set_defaults(func=cmd_restore)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, StorageError, ApiError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
