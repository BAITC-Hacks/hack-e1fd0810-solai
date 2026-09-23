"""Evidence-based briefs, transparent ordering, and safe optional-model behavior."""

from io import BytesIO
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from pandas.testing import assert_frame_equal

from src.data_loader import load_sample_data
from src.forecasting import forecast_demand
from src.replenishment import calculate_replenishment
from src.decision_intelligence import (executive_brief, sku_analysis, priority_queue, filter_recommendations,
    portfolio_metrics, data_quality_summary, quality_label, key_signals, context_fingerprint)
from src.copilot import brief_sections, llm_brief, llm_available

ROOT = Path(__file__).resolve().parents[1]


class DecisionIntelligenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = load_sample_data(ROOT / "data/sample_data.csv")
        data["current_stock"] = 0
        data["in_transit"] = 0
        cls.forecasts, cls.audit = forecast_demand(data)
        cls.recommendations = calculate_replenishment(cls.forecasts, cls.audit)

    def test_brief_uses_calculated_totals_and_filtered_scope(self):
        rows = self.recommendations.iloc[:3]
        result = executive_brief(rows)
        m = result["metrics"]
        self.assertEqual(m["skus"], 3)
        self.assertEqual(m["recommended_units"], rows.recommended_order_qty.sum())
        self.assertEqual(m["requiring_order"], rows.recommended_order_qty.gt(0).sum())
        self.assertEqual(sum(s["units"] for s in result["suppliers"]), m["recommended_units"])
        self.assertTrue(set(r["sku"] for r in result["attention"]).issubset(set(rows.sku)))
        rendered = brief_sections(result)
        self.assertIn("3 SKUs", rendered["portfolio"][0])
        self.assertEqual(result["data_risks"], data_quality_summary(rows))

    def test_analysis_never_invents_missing_input_or_confidence(self):
        row = self.recommendations.iloc[0].copy()
        for field in ["lead_time_days", "current_stock", "in_transit", "recommended_order_qty", "current_coverage_days"]:
            row[field] = pd.NA
        result = sku_analysis(row)
        self.assertIsNone(result["values"]["recommended_order_qty"])
        self.assertIsNone(result["values"]["lead_time_days"])
        self.assertEqual(result["quality"], "review_required")
        self.assertEqual(result["next_action"], "confirm_missing_inputs")
        self.assertNotIn("confidence_probability", result)

    def test_unknown_order_total_is_not_zero(self):
        rows = self.recommendations.iloc[:2].copy()
        rows["recommended_order_qty"] = pd.NA
        metrics = portfolio_metrics(rows)
        self.assertIsNone(metrics["recommended_units"])
        self.assertEqual(metrics["unknown_orders"], 2)
        lines = brief_sections(executive_brief(rows))["portfolio"][0]
        self.assertIn("Not available", lines)
        rows.loc[rows.index[0], "recommended_order_qty"] = 0
        self.assertEqual(portfolio_metrics(rows)["recommended_units"], 0)
        self.assertEqual(portfolio_metrics(rows)["unknown_orders"], 1)

    def test_priority_order_is_transparent_and_stable(self):
        rows = self.recommendations.iloc[:4].copy()
        rows["sku"] = ["D", "C", "B", "A"]
        rows["urgency"] = ["LOW", "HIGH", "HIGH", "CRITICAL"]
        rows["status"] = "REVIEW"
        rows["current_coverage_days"] = [1, 10, 2, 100]
        for seed in range(4):
            queue = priority_queue(rows.sample(frac=1, random_state=seed))
            self.assertEqual(queue.sku.tolist(), ["A", "B", "C", "D"])
            self.assertTrue(queue.priority_reason.str.len().gt(0).all())
        self.assertNotIn("ai_score", queue)

    def test_signals_respect_anomalies_and_stockout_evidence(self):
        row = self.recommendations.set_index("sku").loc["SNK-440"].copy()
        row["sku"] = "SNK-440"
        signals = key_signals(row, self.audit.loc[self.audit.sku.eq("SNK-440")])
        self.assertIn("stockout", [s["code"] for s in signals])
        self.assertIn("lead_exposure", [s["code"] for s in signals])
        row["stockout_periods"] = 0
        self.assertNotIn("stockout", [s["code"] for s in key_signals(row)])
        row["raw_required_qty"], row["recommended_order_qty"] = 137, 140
        constraint = next(s for s in key_signals(row) if s["code"] == "constraint")
        self.assertEqual((constraint["raw"], constraint["final"]), (137, 140))

    def test_decline_requires_multiple_clean_consecutive_periods(self):
        row = self.recommendations.iloc[0].copy()
        row["forecast_month"] = pd.Timestamp("2026-07-01")
        history = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=6, freq="MS"),
                                "sales": [200, 180, 160, 140, 120, 100], "is_anomaly": False, "is_stockout": False})
        self.assertIn("sales_decline", [s["code"] for s in key_signals(row, history)])
        history["sales"] = [1000, 100, 100, 100, 100, 100]
        self.assertNotIn("sales_decline", [s["code"] for s in key_signals(row, history)])
        history.loc[0, "is_anomaly"] = True
        self.assertNotIn("sales_decline", [s["code"] for s in key_signals(row, history)])

    def test_filtering_uses_literal_search_and_all_matching_rows(self):
        rows = self.recommendations
        selected = rows.iloc[0]
        filtered = filter_recommendations(rows, search=selected.sku.lower(), supplier=selected.supplier,
                                         urgencies=[selected.urgency], statuses=[selected.status])
        self.assertEqual(filtered.sku.tolist(), [selected.sku])
        self.assertTrue(filter_recommendations(rows, search="[.*").empty)
        self.assertEqual(executive_brief(filtered)["metrics"]["skus"], 1)

    def test_intelligence_is_read_only_and_context_invalidates(self):
        rows, audit = self.recommendations.copy(deep=True), self.audit.copy(deep=True)
        executive_brief(rows)
        for _, row in rows.iterrows():
            sku_analysis(row, audit.loc[audit.sku.eq(row.sku)])
        assert_frame_equal(rows, self.recommendations)
        assert_frame_equal(audit, self.audit)
        first = context_fingerprint(rows, "en")
        self.assertEqual(first, context_fingerprint(rows.iloc[::-1], "en"))
        self.assertNotEqual(first, context_fingerprint(rows, "ru"))
        self.assertNotEqual(first, context_fingerprint(rows.iloc[:1], "en"))

    def test_quality_labels_have_explicit_rules(self):
        row = self.recommendations.iloc[0].copy()
        row["status"], row["review_reasons"] = "ORDER", ""
        row["baseline_observations"], row["history_observations"] = 6, 12
        row["seasonal_source"], row["stockout_metadata_missing"] = "sku", 0
        self.assertEqual(quality_label(row)[0], "strong_data")
        row["seasonal_source"] = "none"
        self.assertEqual(quality_label(row)[0], "limited_data")
        row["lead_time_days"] = None
        self.assertEqual(quality_label(row)[0], "review_required")

    def test_no_api_key_means_no_network_and_complete_brief(self):
        opener = Mock()
        with patch.dict("os.environ", {}, clear=True):
            sections = brief_sections(executive_brief(self.recommendations))
            self.assertFalse(llm_available())
            lines, error = llm_brief(sections, opener=opener)
        self.assertEqual(lines, [])
        self.assertIn("No optional", error)
        opener.assert_not_called()
        self.assertIn("portfolio", sections)

    def test_optional_model_can_only_select_verified_sentences(self):
        env = {"SOLAI_LLM_ENDPOINT": "https://provider.invalid/chat/completions", "SOLAI_LLM_MODEL": "test", "SOLAI_LLM_API_KEY": "test-only"}
        sections = {"portfolio": ["Actual calculated quantity: 137."], "next": ["Manager review is required."]}
        def response(content):
            return BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode())
        with patch.dict("os.environ", env, clear=True):
            lines, error = llm_brief(sections, opener=Mock(return_value=response({"fact_ids": ["F1", "F0"]})))
            self.assertIsNone(error)
            self.assertEqual(lines, [sections["next"][0], sections["portfolio"][0]])
            for invalid in [{"fact_ids": ["invented 999"]}, {"fact_ids": ["F0"], "claim": "99% confidence"}]:
                lines, error = llm_brief(sections, opener=Mock(return_value=response(invalid)))
                self.assertEqual(lines, [])
                self.assertIn("unverified", error)
            self.assertEqual(llm_brief(sections, opener=Mock(side_effect=TimeoutError))[0], [])


if __name__ == "__main__":
    unittest.main()
