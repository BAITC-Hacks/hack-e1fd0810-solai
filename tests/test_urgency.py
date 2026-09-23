"""Deterministic urgency levels and their relationship to coverage."""

import unittest

from src.replenishment import calculate_urgency, calculate_replenishment, supplier_proposals
from src.data_loader import load_sample_data
from src.explanations import explain_replenishment
from pathlib import Path


class UrgencyTests(unittest.TestCase):
    def test_critical_total_coverage_shorter_than_lead(self):
        row = calculate_urgency(300, 50, 20, 14)
        self.assertEqual(row["urgency"], "CRITICAL")
        self.assertEqual(row["current_coverage_days"], 5)
        self.assertEqual(row["position_coverage_days"], 7)
        self.assertIn("cannot cover supplier lead time", row["urgency_reason"])

    def test_no_current_stock_is_critical_even_with_transit(self):
        self.assertEqual(calculate_urgency(300, 0, 1000, 14)["urgency"], "CRITICAL")

    def test_transit_covering_lead_time_still_requires_attention(self):
        row = calculate_urgency(300, 50, 100, 14)
        self.assertEqual(row["urgency"], "HIGH")
        self.assertIn("in-transit", row["urgency_reason"])

    def test_exact_boundaries(self):
        self.assertEqual(calculate_urgency(300, 140, 0, 14)["urgency"], "MEDIUM")
        self.assertEqual(calculate_urgency(300, 209, 0, 14)["urgency"], "MEDIUM")
        self.assertEqual(calculate_urgency(300, 210, 0, 14)["urgency"], "LOW")

    def test_zero_demand_and_invalid_data(self):
        row = calculate_urgency(0, 0, 0, 14)
        self.assertEqual(row["urgency"], "LOW")
        self.assertIsNone(row["position_coverage_days"])
        for demand in [-1, None, float("nan"), float("inf")]:
            row = calculate_urgency(demand, 20, 0, 14)
            self.assertEqual(row["urgency"], "HIGH")
            self.assertIn("not confirmed", row["urgency_reason"])

    def test_longer_lead_time_and_more_demand_raise_urgency(self):
        self.assertEqual(calculate_urgency(300, 210, 0, 14)["urgency"], "LOW")
        self.assertEqual(calculate_urgency(300, 210, 0, 30)["urgency"], "CRITICAL")
        self.assertEqual(calculate_urgency(600, 210, 0, 14)["urgency"], "CRITICAL")

    def test_urgency_flows_to_explanations_and_supplier_lines(self):
        from src.forecasting import forecast_demand
        data = load_sample_data(Path(__file__).resolve().parents[1] / "data/sample_data.csv")
        data["current_stock"] = 0
        data["in_transit"] = 0
        forecasts, audit = forecast_demand(data)
        result = calculate_replenishment(forecasts, audit)
        self.assertTrue(result.urgency.eq("CRITICAL").all())
        self.assertIn("Urgency: CRITICAL", explain_replenishment(result.iloc[0]))
        lines, totals = supplier_proposals(result)
        self.assertTrue(lines.urgency.eq("CRITICAL").all())
        self.assertTrue(totals.urgency.eq("CRITICAL").all())


if __name__ == "__main__":
    unittest.main()
