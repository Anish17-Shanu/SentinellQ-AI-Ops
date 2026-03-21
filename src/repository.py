import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from engine import normalize_incident


class IncidentRepository:
    def __init__(self, data_path=None):
        self.data_path = data_path or Path(__file__).resolve().parents[1] / "data" / "incidents.json"

    def _read(self):
        with self.data_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write(self, items):
        with self.data_path.open("w", encoding="utf-8") as handle:
            json.dump(items, handle, indent=2)

    def list(self, priority=None, owner=None, environment=None):
        items = self._read()
        filtered = []
        for item in items:
            if priority and item.get("priority_hint", "").upper() != priority.upper():
                continue
            if owner and item.get("owner", "").lower() != owner.lower():
                continue
            if environment and item.get("environment", "").lower() != environment.lower():
                continue
            filtered.append(item)
        return filtered

    def create(self, payload):
        incident = normalize_incident(payload)
        incident["id"] = str(uuid4())
        incident["created_at"] = datetime.utcnow().isoformat() + "Z"
        incident["priority_hint"] = payload.get("priority_hint", "").upper()
        items = self._read()
        items.insert(0, incident)
        self._write(items)
        return incident
