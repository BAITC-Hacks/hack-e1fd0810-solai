"""Regression coverage for current results and legacy Streamlit module imports."""

from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src import forecasting
from src.data_loader import load_sample_data
from src.engine import load_engine


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COLUMNS = [
    "sku", "product_name", "category", "supplier", "baseline_demand",
    "seasonal_factor", "forecast_demand", "anomalies_detected", "current_stock",
    "in_transit", "lead_time_days", "raw_demand_estimate", "forecast_month",
    "seasonal_source", "baseline_observations", "baseline_anomalies_excluded",
    "raw_sales_baseline", "anomaly_adjustment", "stockout_adjustment", "stockout_periods",
    "estimated_lost_demand", "stockout_unresolved", "stockout_fallback_periods",
    "stockout_metadata_missing", "seasonal_adjustment", "growth_factor",
    "growth_adjustment", "growth_detected", "growth_slope", "growth_observations", "growth_reason",
]


class ForecastIntegrationTests(unittest.TestCase):
    def test_actual_result_schema_and_adjustment_values(self):
        engine = load_engine()
        forecasts, audit = engine.forecasting.forecast_demand(load_sample_data(ROOT / "data/sample_data.csv"))
        self.assertEqual(forecasts.columns.tolist(), EXPECTED_COLUMNS)
        self.assertEqual(len(forecasts), 8)
        by_sku = forecasts.set_index("sku")
        self.assertEqual(by_sku.loc["SNK-440", "stockout_adjustment"], 14)
        self.assertGreater(by_sku.loc["SNK-440", "estimated_lost_demand"], 0)
        self.assertGreater(by_sku.loc["BEV-220", "growth_factor"], 1)
        self.assertIn("adjusted_demand", audit.columns)

    def test_existing_streamlit_session_refreshes_legacy_engine(self):
        app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        # Reproduce a process holding the old 16-column producer while app.py
        # expects the upgraded stockout/growth contract. No disk files change.
        current_forecast = forecasting.forecast_demand

        def legacy_forecast(*args, **kwargs):
            frame, audit = current_forecast(*args, **kwargs)
            return frame[EXPECTED_COLUMNS[:16]], audit

        with patch.object(forecasting, "FORECAST_SCHEMA_VERSION", 1), \
                patch.object(forecasting, "SUMMARY_COLUMNS", EXPECTED_COLUMNS[:16]), \
                patch.object(forecasting, "forecast_demand", side_effect=legacy_forecast) as legacy:
            app.run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            legacy.assert_not_called()
            table = app.dataframe[1].value.set_index("sku")
            self.assertEqual(table.loc["SNK-440", "raw_sales_baseline"], 295)
            self.assertEqual(table.loc["SNK-440", "stockout_adjustment"], 14)
            self.assertGreater(table.loc["SNK-440", "estimated_lost_demand"], 0)
            self.assertGreater(table.loc["BEV-220", "growth_factor"], 1)
            self.assertIn("Replenishment Recommendations", [item.value for item in app.subheader])

    def test_current_engine_is_not_reloaded_on_ordinary_rerun(self):
        load_engine()
        with patch("src.engine.importlib.reload") as reload:
            load_engine()
            reload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
