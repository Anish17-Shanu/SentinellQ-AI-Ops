import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from engine import normalize_incident, score_incident, summarize_incidents


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


if __name__ == "__main__":
    unittest.main()
