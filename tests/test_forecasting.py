"""Run with: python -m unittest discover -s tests -v."""

import unittest
from pathlib import Path

import pandas as pd
from pandas.testing import assert_frame_equal
from streamlit.testing.v1 import AppTest
from ui_helpers import forecast_table

from src.anomaly_detection import detect_sales_anomalies
from src.data_loader import load_sample_data
from src.forecasting import explain_forecast, forecast_demand


ROOT = Path(__file__).resolve().parents[1]


def history(sales, sku="A", start="2024-01-01"):
    return pd.DataFrame({
        "date": pd.date_range(start, periods=len(sales), freq="MS"),
        "sku": sku, "sales": sales, "product_name": "Product " + sku,
        "category": "Category", "supplier": "Supplier", "current_stock": 40,
        "in_transit": 20, "lead_time_days": 7,
    })


class ForecastTests(unittest.TestCase):
    def test_normal_history_sorted_and_unchanged(self):
        data = history([98, 100, 102, 99, 101, 100]).sample(frac=1, random_state=2)
        original = data.copy(deep=True)
        summary, audit = forecast_demand(data)
        self.assertEqual(summary.iloc[0].baseline_demand, 100)
        self.assertEqual(summary.iloc[0].forecast_demand, 100)
        self.assertFalse(audit.is_anomaly.any())
        self.assertTrue(audit.date.is_monotonic_increasing)
        assert_frame_equal(data, original)

    def test_extreme_spike_retained_but_excluded(self):
        summary, audit = forecast_demand(history([100, 101, 99, 100, 102, 10000]))
        row = summary.iloc[0]
        self.assertEqual(row.baseline_demand, 100)
        self.assertGreater(row.raw_demand_estimate, 1700)
        self.assertEqual(row.anomalies_detected, 1)
        self.assertEqual(row.baseline_anomalies_excluded, 1)
        self.assertEqual(len(audit), 6)
        self.assertEqual(audit.iloc[-1].anomaly_reason, "high_sales_spike")
        self.assertTrue(audit.iloc[-1].is_anomaly)

    def test_insufficient_history(self):
        summary, audit = forecast_demand(history([12]))
        row = summary.iloc[0]
        self.assertEqual(row.forecast_demand, 12)
        self.assertEqual(row.seasonal_factor, 1)
        self.assertEqual(row.seasonal_source, "none")
        self.assertFalse(audit.is_anomaly.any())

    def test_zero_mad_and_zero_sales(self):
        for normal in [0, 100]:
            summary, audit = forecast_demand(history([normal] * 7 + [10000]))
            self.assertEqual(summary.iloc[0].baseline_demand, normal)
            self.assertEqual(audit.is_anomaly.sum(), 1)

    def test_seasonal_adjustment(self):
        summary, _ = forecast_demand(history([120] + [100] * 11 + [120] + [100] * 11))
        row = summary.iloc[0]
        self.assertEqual(row.seasonal_source, "sku")
        self.assertAlmostEqual(row.seasonal_factor, 1.2)
        self.assertAlmostEqual(row.forecast_demand, 120)
        self.assertIn("20.0%", explain_forecast(row))

    def test_adjustment_relative_to_recent_season(self):
        pattern = [120] * 6 + [100] * 6
        summary, _ = forecast_demand(history(pattern * 2 + [120] * 6))
        row = summary.iloc[0]
        self.assertEqual(row.baseline_demand, 120)
        self.assertAlmostEqual(row.forecast_demand, 100)

    def test_category_fallback_normalizes_peer_scale(self):
        peer = history(([1200] + [1000] * 11) * 2, sku="B")
        short = history([100] * 6, start="2025-07-01")
        summary, _ = forecast_demand(pd.concat([peer, short]))
        row = summary.set_index("sku").loc["A"]
        self.assertEqual(row.seasonal_source, "category")
        self.assertAlmostEqual(row.forecast_demand, 120)

    def test_future_peer_data_not_used(self):
        peer = history(([1200] + [1000] * 11) * 2, sku="B", start="2026-01-01")
        summary, _ = forecast_demand(pd.concat([peer, history([100] * 6)]))
        self.assertEqual(summary.set_index("sku").loc["A"].seasonal_source, "none")

    def test_skus_independent_latest_inventory_and_calendar_window(self):
        first = history([10] * 6)
        first.loc[5, "current_stock"] = 77
        second = history([1000] * 6, sku="B")
        summary, _ = forecast_demand(pd.concat([first, second]).sample(frac=1))
        self.assertEqual(summary.set_index("sku").loc["A"].current_stock, 77)
        self.assertEqual(summary.set_index("sku").loc["B"].baseline_demand, 1000)
        gapped = history([10, 20])
        gapped.loc[1, "date"] = pd.Timestamp("2025-01-01")
        summary, _ = forecast_demand(gapped)
        self.assertEqual(summary.iloc[0].baseline_demand, 20)

    def test_invalid_monthly_input(self):
        for sales in [[-1], [float("inf")], [float("nan")]]:
            with self.assertRaises(ValueError):
                detect_sales_anomalies(history(sales))
        with self.assertRaises(ValueError):
            forecast_demand(pd.concat([history([10]), history([20])]))

    def test_sample_data(self):
        data = load_sample_data(ROOT / "data/sample_data.csv")
        summary, audit = forecast_demand(data)
        self.assertEqual(len(summary), data.sku.nunique())
        self.assertEqual(len(audit), len(data))
        self.assertTrue(summary.seasonal_factor.eq(1).all())
        self.assertTrue(summary.forecast_demand.ge(0).all())

    def test_streamlit_renders_and_sku_selection_works(self):
        app = AppTest.from_file(str(ROOT / "app.py"))
        app.session_state["data_source"] = "Synthetic demo"
        app.run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertIn("Demand Forecast", [item.value for item in app.subheader])
        self.assertEqual(len(forecast_table(app)), 8)
        app.selectbox(key="selected_sku").select("SNK-440").run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.selectbox(key="selected_sku").value, "SNK-440")


if __name__ == "__main__":
    unittest.main()
