"""Demo decisions stay separate from algorithm output and survive UI reruns."""

from pathlib import Path
import unittest

import pandas as pd
from pandas.testing import assert_frame_equal
from streamlit.testing.v1 import AppTest
from ui_helpers import forecast_table

from src.manager_review import sync_review_state, set_review_quantity, approve_review, reviewed_recommendations
from src.data_loader import load_sample_data
from src.forecasting import forecast_demand
from src.replenishment import calculate_replenishment, supplier_proposals


ROOT = Path(__file__).resolve().parents[1]


def recommendations():
    return calculate_replenishment(*forecast_demand(load_sample_data(ROOT / "data/sample_data.csv")))


def quantity_widget(app):
    return next(widget for widget in app.number_input if widget.label == "Manager order quantity")


def review_table(app):
    return next(table.value for table in app.dataframe if "decision_state" in table.value and "quantity_changed" in table.value)


class ManagerReviewTests(unittest.TestCase):
    def test_edit_preserves_original_and_changes_supplier_totals(self):
        calculated = recommendations()
        original = calculated.copy(deep=True)
        state = sync_review_state(calculated, {})
        state = set_review_quantity(state, "BEV-100", 25)
        reviewed = reviewed_recommendations(calculated, state)
        row = reviewed.set_index("sku").loc["BEV-100"]
        self.assertEqual(row.recommended_order_qty, 0)
        self.assertEqual(row.manager_order_qty, 25)
        self.assertTrue(row.quantity_changed)
        self.assertEqual(row.decision_state, "Pending")
        lines, totals = supplier_proposals(reviewed, "manager_order_qty")
        self.assertEqual(lines.iloc[0].recommended_order_qty, 0)
        self.assertEqual(totals.iloc[0].total_units, 25)
        self.assertAlmostEqual(totals.iloc[0].estimated_order_value, 25 * 6.49)
        assert_frame_equal(calculated, original)

    def test_approval_and_rerun_preserve_decision(self):
        calculated = recommendations()
        state = sync_review_state(calculated, {})
        approved = approve_review(state, "BEV-100")
        self.assertEqual(state["BEV-100"]["decision_state"], "Pending")
        self.assertEqual(approved["BEV-100"]["approved_quantity"], 0)
        self.assertIsNotNone(approved["BEV-100"]["approved_at"])
        self.assertEqual(sync_review_state(calculated, approved), approved)
        self.assertEqual(approve_review(approved, "BEV-100"), approved)

    def test_edit_after_approval_returns_to_pending(self):
        state = approve_review(sync_review_state(recommendations(), {}), "BEV-100")
        state = set_review_quantity(state, "BEV-100", 25)
        record = state["BEV-100"]
        self.assertEqual(record["decision_state"], "Pending")
        self.assertIsNone(record["approved_at"])
        self.assertEqual([event["action"] for event in record["history"]], ["approved_demo_only", "quantity_changed"])

    def test_calculation_change_invalidates_approval_but_preserves_override(self):
        calculated = recommendations()
        state = set_review_quantity(sync_review_state(calculated, {}), "BEV-100", 25)
        state = approve_review(state, "BEV-100")
        changed = calculated.copy()
        changed.loc[changed.sku.eq("BEV-100"), "recommended_order_qty"] = 10
        state = sync_review_state(changed, state)
        self.assertEqual(state["BEV-100"]["manager_order_qty"], 25)
        self.assertEqual(state["BEV-100"]["original_order_qty"], 10)
        self.assertEqual(state["BEV-100"]["decision_state"], "Pending")
        self.assertTrue(state["BEV-100"]["calculation_changed"])

    def test_untouched_quantity_follows_new_calculation(self):
        calculated = recommendations()
        state = sync_review_state(calculated, {})
        calculated.loc[calculated.sku.eq("BEV-100"), "recommended_order_qty"] = 10
        state = sync_review_state(calculated, state)
        self.assertEqual(state["BEV-100"]["manager_order_qty"], 10)

    def test_invalid_quantities_and_unknown_calculation(self):
        calculated = recommendations()
        calculated.loc[calculated.sku.eq("BEV-100"), "recommended_order_qty"] = pd.NA
        state = sync_review_state(calculated, {})
        with self.assertRaises(ValueError):
            approve_review(state, "BEV-100")
        for value in [-1, 1.5, float("nan"), float("inf"), None, True]:
            with self.assertRaises(ValueError):
                set_review_quantity(state, "BEV-100", value)
        self.assertEqual(approve_review(set_review_quantity(state, "BEV-100", 5), "BEV-100")["BEV-100"]["decision_state"], "Approved")

    def test_streamlit_edit_approve_switch_sku_and_recalculate(self):
        app = AppTest.from_file(str(ROOT / "app.py"))
        app.session_state["data_source"] = "Synthetic demo"
        app.run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        app.selectbox(key="selected_sku").select("SNK-440").run(timeout=30)
        original = review_table(app).set_index("sku").loc["SNK-440", "recommended_order_qty"]
        quantity_widget(app).set_value(25).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        row = review_table(app).set_index("sku").loc["SNK-440"]
        self.assertEqual(row.manager_order_qty, 25)
        self.assertEqual(row.recommended_order_qty, original)
        self.assertTrue(row.quantity_changed)
        self.assertIn(row.urgency, ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
        next(button for button in app.button if button.label == "Approve reviewed quantity").click().run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(review_table(app).set_index("sku").loc["SNK-440", "decision_state"], "Approved")
        self.assertTrue(any("No supplier order was sent" in item.value for item in app.success))
        app.run(timeout=30)
        app.selectbox(key="selected_sku").select("BEV-100").run(timeout=30)
        app.selectbox(key="selected_sku").select("SNK-440").run(timeout=30)
        self.assertEqual(quantity_widget(app).value, 25)
        self.assertEqual(review_table(app).set_index("sku").loc["SNK-440", "decision_state"], "Approved")
        quantity_widget(app).set_value(26).run(timeout=30)
        self.assertEqual(review_table(app).set_index("sku").loc["SNK-440", "decision_state"], "Pending")
        next(button for button in app.button if button.label == "Approve reviewed quantity").click().run(timeout=30)
        app.number_input(key="service_factor").set_value(2.0).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        row = review_table(app).set_index("sku").loc["SNK-440"]
        self.assertEqual(row.manager_order_qty, 26)
        self.assertEqual(row.decision_state, "Pending")
        self.assertTrue({"raw_sales_baseline", "stockout_adjustment", "estimated_lost_demand", "growth_factor"}.issubset(forecast_table(app).columns))


if __name__ == "__main__":
    unittest.main()
