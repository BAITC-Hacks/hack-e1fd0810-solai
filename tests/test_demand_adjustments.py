"""Stockout and growth behavior through the existing forecast/order pipeline."""

from pathlib import Path
import unittest

import pandas as pd
from pandas.testing import assert_frame_equal
from streamlit.testing.v1 import AppTest

from src.data_loader import load_sample_data
from src.forecasting import forecast_demand, explain_forecast
from src.replenishment import calculate_replenishment
from src.explanations import explain_replenishment


ROOT = Path(__file__).resolve().parents[1]


def history(sales, days=None, sku="A"):
    data = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=len(sales), freq="MS"),
        "sku": sku, "product_name": "Product", "category": "Category",
        "supplier": "Supplier", "sales": sales, "current_stock": 0,
        "in_transit": 0, "lead_time_days": 30, "unit_price": 2.0,
    })
    if days is not None:
        data["stockout_days"] = days
    return data


class DemandAdjustmentTests(unittest.TestCase):
    def test_stockout_increases_demand_and_order_without_replacing_sales(self):
        data = history([310, 290, 310, 150, 150, 150], [0, 0, 0, 15, 16, 15])
        original = data.copy(deep=True)
        compensated, audit = forecast_demand(data)
        raw, raw_audit = forecast_demand(data.drop(columns="stockout_days"))
        row = compensated.iloc[0]
        self.assertGreater(row.forecast_demand, raw.iloc[0].forecast_demand)
        self.assertGreater(row.forecast_demand, row.raw_demand_estimate)
        self.assertAlmostEqual(audit.iloc[-1].stockout_daily_rate, 10)
        self.assertEqual(audit.iloc[-1].estimated_lost_demand, 150)
        self.assertEqual(audit.iloc[-1].adjusted_demand, 300)
        self.assertEqual(audit.iloc[-1].sales, 150)
        adjusted_order = calculate_replenishment(compensated, audit, service_level_factor=0).iloc[0]
        raw_order = calculate_replenishment(raw, raw_audit, service_level_factor=0).iloc[0]
        self.assertGreater(adjusted_order.recommended_order_qty, raw_order.recommended_order_qty)
        self.assertEqual(adjusted_order.stockout_observations_excluded, 3)
        self.assertEqual(adjusted_order.status, "REVIEW")
        assert_frame_equal(data, original)

    def test_full_month_stockout_is_not_zero_demand(self):
        summary, audit = forecast_demand(history([310, 290, 310, 0], [0, 0, 0, 30]))
        self.assertEqual(audit.iloc[-1].adjusted_demand, 300)
        self.assertEqual(audit.iloc[-1].estimated_lost_demand, 300)
        self.assertFalse(audit.is_anomaly.any())
        self.assertGreater(summary.iloc[0].baseline_demand, 0)

    def test_spike_not_used_as_lost_demand_comparator(self):
        _, audit = forecast_demand(history([310, 290, 310, 300, 310, 100000, 0], [0, 0, 0, 0, 0, 0, 31]))
        self.assertTrue(audit.iloc[-2].is_anomaly)
        self.assertAlmostEqual(audit.iloc[-1].adjusted_demand, 310)

    def test_stockouts_do_not_make_regular_sales_look_like_spikes(self):
        data = history([310, 290, 310, 300, 0, 0, 0, 0, 0, 0, 0, 0])
        data["stockout_days"] = [0] * 4 + data.date.dt.days_in_month.iloc[4:].tolist()
        _, audit = forecast_demand(data)
        self.assertFalse(audit.is_anomaly.any())
        self.assertTrue(audit.adjusted_demand.gt(0).all())

    def test_unresolvable_stockout_is_review_not_zero_coverage(self):
        summary, audit = forecast_demand(history([0], [31]))
        self.assertTrue(pd.isna(summary.iloc[0].forecast_demand))
        self.assertEqual(summary.iloc[0].stockout_unresolved, 1)
        result = calculate_replenishment(summary, audit).iloc[0]
        self.assertEqual(result.status, "REVIEW")
        self.assertTrue(pd.isna(result.recommended_order_qty))
        self.assertIn("unresolved", explain_forecast(summary.iloc[0]))

    def test_own_exposure_fallback_and_validation(self):
        summary, audit = forecast_demand(history([210], [10]))
        self.assertEqual(audit.iloc[0].adjusted_demand, 310)
        self.assertEqual(summary.iloc[0].stockout_fallback_periods, 1)
        for days in [-1, 32, 1.5, float("inf")]:
            with self.assertRaises(ValueError):
                forecast_demand(history([100], [days]))
        with self.assertRaises(ValueError):
            forecast_demand(history([100], [31]))

    def test_legacy_missing_metadata_does_not_infer_stockout(self):
        summary, audit = forecast_demand(history([0] * 6))
        self.assertEqual(summary.iloc[0].forecast_demand, 0)
        self.assertEqual(summary.iloc[0].stockout_metadata_missing, 6)
        self.assertFalse(audit.is_stockout.any())

    def test_sustained_growth_increases_forecast_and_order(self):
        summary, audit = forecast_demand(history([100, 120, 140, 160, 180, 200]))
        row = summary.iloc[0]
        self.assertTrue(row.growth_detected)
        self.assertEqual(row.baseline_demand, 150)
        self.assertAlmostEqual(row.forecast_demand, 220)
        self.assertAlmostEqual(row.growth_slope, 20)
        self.assertGreater(row.growth_factor, 1)
        self.assertEqual(calculate_replenishment(summary, audit, 0).iloc[0].recommended_order_qty, 220)

    def test_long_flat_history_does_not_hide_new_growth(self):
        summary, audit = forecast_demand(history([100] * 24 + [120, 140, 160, 180, 200, 220]))
        self.assertFalse(audit.tail(6).is_anomaly.any())
        self.assertTrue(summary.iloc[0].growth_detected)
        self.assertGreater(summary.iloc[0].forecast_demand, summary.iloc[0].baseline_demand)

    def test_single_bulk_order_does_not_create_growth(self):
        for peak in [140, 100000]:
            summary, _ = forecast_demand(history([100] * 8 + [peak]))
            self.assertFalse(summary.iloc[0].growth_detected)
            self.assertEqual(summary.iloc[0].growth_factor, 1)
            self.assertEqual(summary.iloc[0].forecast_demand, 100)

    def test_growth_survives_an_isolated_bulk_order(self):
        summary, audit = forecast_demand(history([100, 120, 10000, 160, 180, 200, 220]))
        self.assertEqual(audit.is_anomaly.sum(), 1)
        self.assertTrue(summary.iloc[0].growth_detected)
        self.assertAlmostEqual(summary.iloc[0].forecast_demand, 240)

    def test_imputed_stockout_recovery_is_not_growth(self):
        summary, _ = forecast_demand(history([10, 30, 60, 80, 100, 100], [28, 20, 10, 5, 0, 0]))
        self.assertFalse(summary.iloc[0].growth_detected)
        self.assertEqual(summary.iloc[0].growth_observations, 2)

    def test_known_seasonality_is_not_counted_again_as_growth(self):
        pattern = [100, 120, 140, 160, 180, 200] + [150] * 6
        summary, _ = forecast_demand(history(pattern * 2 + pattern[:6]))
        self.assertEqual(summary.iloc[0].seasonal_source, "sku")
        self.assertFalse(summary.iloc[0].growth_detected)

    def test_growth_requires_recent_evidence_and_is_capped(self):
        summary, _ = forecast_demand(history([10, 20, 40, 80, 160, 320]))
        self.assertTrue(summary.iloc[0].growth_detected)
        self.assertEqual(summary.iloc[0].growth_factor, 1.5)
        summary, _ = forecast_demand(history([100, 120, 140]))
        self.assertEqual(summary.iloc[0].growth_factor, 1)

    def test_skus_are_independent_and_adjustments_reconcile(self):
        data = pd.concat([history([100, 120, 140, 160, 180, 200]), history([300] * 6, sku="B")])
        summary, _ = forecast_demand(data.sample(frac=1, random_state=9))
        self.assertEqual(summary.set_index("sku").loc["B"].growth_factor, 1)
        for _, row in summary.iterrows():
            self.assertAlmostEqual(row.raw_demand_estimate + row.anomaly_adjustment + row.stockout_adjustment
                                   + row.seasonal_adjustment + row.growth_adjustment, row.forecast_demand)

    def test_sample_and_ui_expose_adjustments_and_final_quantity(self):
        data = load_sample_data(ROOT / "data/sample_data.csv")
        summary, audit = forecast_demand(data)
        self.assertEqual(summary.stockout_periods.sum(), 3)
        self.assertTrue(summary.set_index("sku").loc["BEV-220"].growth_detected)
        self.assertGreater(summary.set_index("sku").loc["SNK-440"].stockout_adjustment, 0)
        result = calculate_replenishment(summary, audit)
        text = explain_replenishment(result.set_index("sku").loc["BEV-220"])
        self.assertIn("Sustainable growth", text)
        app = AppTest.from_file(str(ROOT / "app.py"))
        app.session_state["data_source"] = "Synthetic demo"
        app.run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        app.selectbox(key="selected_sku").select("SNK-440").run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        details = next(item.value for item in app.dataframe if "Calculation" in item.value)
        self.assertTrue({"baseline_demand", "anomaly_adjustment", "seasonal_adjustment",
                         "stockout_adjustment", "growth_adjustment", "forecast_demand",
                         "recommended_order_qty"}.issubset(set(details.Calculation)))
        self.assertTrue(any("Stockout compensation" in item.value for item in app.markdown))


if __name__ == "__main__":
    unittest.main()
