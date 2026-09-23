"""Decision workspace interactions without changing validated business outputs."""

from pathlib import Path
import os
import unittest
from unittest.mock import patch

from pandas.testing import assert_frame_equal
import pandas as pd
from streamlit.testing.v1 import AppTest

from src.data_loader import load_sample_data
from src.engine import load_engine
from src.dashboard_data import recommendation_bundle, source_fingerprint
from src.translations import TRANSLATIONS
from ui_helpers import forecast_table

ROOT = Path(__file__).resolve().parents[1]


def demo():
    app = AppTest.from_file(str(ROOT / "app.py"))
    app.session_state["data_source"] = "Synthetic demo"
    app.run(timeout=45)
    return app


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"TRANSLATION_API_KEY": "", "SOLAI_LLM_API_KEY": "", "SOLAI_LLM_ENDPOINT": "", "SOLAI_LLM_MODEL": ""})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def test_cached_orchestration_equals_existing_engine(self):
        engine = load_engine()
        direct_f, direct_a = engine.forecasting.forecast_demand(load_sample_data(ROOT / "data/sample_data.csv"))
        direct_r = engine.replenishment.calculate_replenishment(direct_f, direct_a)
        forecasts, audit, recs = recommendation_bundle("Synthetic demo", source_fingerprint(False), None, None, 1.65)
        assert_frame_equal(forecasts, direct_f)
        assert_frame_equal(audit, direct_a)
        assert_frame_equal(recs.drop(columns="explanation"), direct_r)

    def test_missing_display_is_distinct_from_zero_and_does_not_mutate_inputs(self):
        app = AppTest.from_string('''import pandas as pd
import streamlit as st
from src.presentation import show_table
frame = pd.DataFrame({"recommended_order_qty": pd.array([None, 0, 25], dtype="Int64")})
show_table(frame)
st.session_state["original_values"] = frame
''').run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.dataframe[0].value.recommended_order_qty.tolist(), ["Not available", "0", "25"])
        self.assertTrue(pd.isna(app.session_state["original_values"].iloc[0, 0]))
        self.assertEqual(app.session_state["original_values"].iloc[1, 0], 0)

    def test_brief_analysis_filters_and_stale_context(self):
        app = demo()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual([t.label for t in app.tabs], ["Overview", "Forecast", "Replenishment", "Warehouse", "AI Copilot", "Approvals"])
        self.assertTrue(app.checkbox(key="optional_llm").disabled)
        app.button(key="generate_brief").click().run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertIn("8 SKUs analyzed", app.session_state["decision_brief"]["sections"]["portfolio"][0])
        app.button(key="analyze_sku").click().run(timeout=30)
        self.assertEqual(app.session_state["sku_analysis"]["result"]["sku"], app.selectbox(key="selected_sku").value)
        app.text_input(key="product_search").set_value("SNK-440").run(timeout=30)
        self.assertEqual(forecast_table(app).sku.tolist(), ["SNK-440"])
        self.assertFalse(any("8 SKUs analyzed" in element.value for element in app.markdown))
        app.button(key="generate_brief").click().run(timeout=30)
        self.assertIn("1 SKUs analyzed", app.session_state["decision_brief"]["sections"]["portfolio"][0])
        rec = next(t.value for t in app.dataframe if "target_stock" in t.value and "sku" in t.value).iloc[0]
        app.multiselect(key="urgency_filter").set_value([rec.urgency]).run(timeout=30)
        app.multiselect(key="status_filter").set_value([rec.status]).run(timeout=30)
        self.assertEqual(len(forecast_table(app)), 1)
        app.text_input(key="product_search").set_value("no_such_product").run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("No SKUs match" in info.value for info in app.info))

    def test_language_changes_preserve_approval_and_do_not_recalculate(self):
        app = demo()
        sku = app.selectbox(key="selected_sku").value
        app.number_input(key=f"manager_quantity_{sku}").set_value(25).run(timeout=30)
        app.button(key=f"approve_{sku}").click().run(timeout=30)
        original = app.session_state["manager_reviews"][sku]["original_order_qty"]
        engine = load_engine()
        with patch.object(engine.forecasting, "forecast_demand", wraps=engine.forecasting.forecast_demand) as forecast, \
                patch.object(engine.replenishment, "calculate_replenishment", wraps=engine.replenishment.calculate_replenishment) as calculate:
            for language in ["RU", "KZ", "EN"]:
                app.radio(key="language_choice").set_value(language).run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertIn(TRANSLATIONS[language.lower()]["forecast_heading"], [h.value for h in app.subheader])
                record = app.session_state["manager_reviews"][sku]
                self.assertEqual(record["manager_order_qty"], 25)
                self.assertEqual(record["original_order_qty"], original)
                self.assertEqual(record["decision_state"], "Approved")
            forecast.assert_not_called()
            calculate.assert_not_called()
        self.assertTrue(any("No supplier order was sent" in element.value for element in app.success))


if __name__ == "__main__":
    unittest.main()
