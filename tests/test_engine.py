import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from support import incident

from engine import (
    DEFAULT_CONFIG,
    load_playbooks,
    load_weights,
    normalize_incident,
    resolve_actions,
    score_incident,
    summarize_incidents,
)
from errors import ConfigError, ValidationError


class EngineTests(unittest.TestCase):
    def test_score_returns_p1_for_critical_incident(self):
        result = score_incident(
            {
                "service": "payments-api",
                "criticality": "critical",
                "impacted_users": 18000,
                "error_rate": 18.0,
                "latency_ms": 2100,
                "alerts": 20,
                "category": "availability",
            }
        )
        self.assertEqual(result["priority"], "P1")
        self.assertGreater(result["score"], 85)

    def test_normalize_requires_service(self):
        with self.assertRaises(ValueError):
            normalize_incident({"criticality": "high"})

    def test_summary_counts_priorities(self):
        summary = summarize_incidents(
            [
                {
                    "service": "identity",
                    "criticality": "high",
                    "impacted_users": 5000,
                    "error_rate": 3.0,
                    "latency_ms": 100,
                    "alerts": 8,
                    "category": "security",
                },
                {
                    "service": "cms",
                    "criticality": "low",
                    "impacted_users": 25,
                    "error_rate": 0.2,
                    "latency_ms": 20,
                    "alerts": 1,
                    "category": "latency",
                },
            ]
        )
        self.assertEqual(summary["total_incidents"], 2)
        self.assertEqual(sum(summary["by_priority"].values()), 2)


class ScoringContractTests(unittest.TestCase):
    def test_normalized_score_is_bounded(self):
        result = score_incident(
            incident(impacted_users=1_000_000_000, error_rate=100, latency_ms=3_600_000, alerts=1_000_000)
        )
        self.assertLessEqual(result["score_100"], 100)
        self.assertGreaterEqual(result["score"], 0)
        self.assertLessEqual(result["score"], DEFAULT_CONFIG.score_max)
        self.assertLessEqual(result["confidence"], DEFAULT_CONFIG.confidence_max)

    def test_priority_hint_can_escalate_but_not_downgrade(self):
        low = score_incident(incident(criticality="low", impacted_users=1, error_rate=0, latency_ms=0, alerts=0, priority_hint="P1"))
        self.assertEqual(low["priority"], "P1")
        self.assertIn("operator priority hint", low["factors"])
        critical = score_incident(incident(criticality="critical", impacted_users=20000, error_rate=20, latency_ms=2000, alerts=20, priority_hint="P4"))
        self.assertEqual(critical["priority"], "P1")

    def test_priority_hint_is_validated(self):
        with self.assertRaises(ValidationError):
            normalize_incident(incident(priority_hint="urgent"))

    def test_numeric_bounds_are_enforced(self):
        with self.assertRaises(ValidationError):
            normalize_incident(incident(error_rate=101))
        with self.assertRaises(ValidationError):
            normalize_incident(incident(impacted_users=-1))
        with self.assertRaises(ValidationError):
            normalize_incident(incident(description="x" * 2001))

    def test_environment_and_category_are_normalized(self):
        normalized = normalize_incident(incident(environment="PRODUCTION", category="Availability", criticality="HIGH"))
        self.assertEqual(normalized["environment"], "production")
        self.assertEqual(normalized["category"], "availability")
        self.assertEqual(normalized["criticality"], "high")

    def test_service_playbook_override_wins(self):
        playbooks = load_playbooks(Path(__file__).resolve().parents[1] / "config" / "playbooks.json")
        custom = resolve_actions(playbooks, "payments-api", "availability")
        default = resolve_actions(playbooks, "identity-gateway", "availability")
        self.assertNotEqual(custom, default)
        self.assertTrue(any("payments" in action.lower() for action in custom))

    def test_custom_weights_are_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "weights.json"
            path.write_text(
                json.dumps(
                    {
                        "criticality_weights": {"low": 2, "medium": 6, "high": 12, "critical": 30},
                        "bands": {"P1": 80, "P2": 60, "P3": 40},
                    }
                ),
                encoding="utf-8",
            )
            config = load_weights(path)
            self.assertEqual(config.criticality_weights["critical"], 30)
            result = score_incident(incident(criticality="critical", impacted_users=0, error_rate=0, latency_ms=0, alerts=0), config)
            self.assertEqual(result["priority"], "P4")

    def test_invalid_weights_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "weights.json"
            path.write_text(json.dumps({"criticality_weights": {"low": 1}}), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_weights(path)
            path.write_text(json.dumps({"bands": {"P1": 10, "P2": 20, "P3": 5}}), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_weights(path)

    def test_missing_files_fall_back_to_defaults(self):
        self.assertIs(load_weights(Path("does-not-exist.json")), DEFAULT_CONFIG)
        playbooks = load_playbooks(Path("does-not-exist.json"))
        self.assertIn("availability", playbooks["defaults"])


if __name__ == "__main__":
    unittest.main()
