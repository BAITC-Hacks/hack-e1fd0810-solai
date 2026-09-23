"""Replenishment calculations, explanations, supplier totals and UI integration."""

import math
from pathlib import Path
import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest

from src.explanations import explain_replenishment
from src.forecasting import forecast_demand
from src.replenishment import calculate_replenishment, supplier_proposals


ROOT = Path(__file__).resolve().parents[1]


def inputs(sales=None, stock=0, transit=0, lead=14, price=2.5, sku="A"):
    if sales is None:
        sales = [300] * 12
    data = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=len(sales), freq="MS"),
        "sku": sku, "product_name": "Product " + sku, "category": "Category",
        "supplier": "Supplier", "sales": sales, "current_stock": stock,
        "in_transit": transit, "lead_time_days": lead, "unit_price": price,
    })
    return forecast_demand(data)


def recommend(**kwargs):
    return calculate_replenishment(*inputs(**kwargs)).iloc[0]


class ReplenishmentTests(unittest.TestCase):
    def test_enough_stock(self):
        row = recommend(stock=1000)
        self.assertEqual(row.recommended_order_qty, 0)
        self.assertEqual(row.status, "COVERED")

    def test_transit_prevents_unnecessary_order(self):
        self.assertGreater(recommend(stock=50).recommended_order_qty, 0)
        row = recommend(stock=50, transit=100)
        self.assertEqual(row.recommended_order_qty, 0)
        self.assertEqual(row.inventory_position, 150)
        self.assertEqual(row.status, "COVERED")

    def test_shortage_exact_formula_and_rounding(self):
        row = recommend(stock=80, transit=20)
        self.assertEqual(row.daily_demand, 10)
        self.assertEqual(row.lead_time_demand, 140)
        self.assertEqual(row.safety_stock, 0)
        self.assertEqual(row.target_stock, 140)
        self.assertEqual(row.raw_order_qty, 40)
        self.assertEqual(row.recommended_order_qty, 40)
        self.assertEqual(row.status, "ORDER")
        self.assertEqual(recommend(sales=[301] * 12, lead=1).recommended_order_qty, 11)

    def test_never_negative(self):
        for stock in [0, 10, 140, 141, 100000]:
            for transit in [0, 1000]:
                self.assertGreaterEqual(recommend(stock=stock, transit=transit).recommended_order_qty, 0)

    def test_longer_lead_time_increases_need(self):
        short = recommend(lead=7)
        long = recommend(lead=28)
        self.assertGreater(long.recommended_order_qty, short.recommended_order_qty)

    def test_variability_increases_safety_stock(self):
        stable = recommend(sales=[300] * 12)
        volatile = recommend(sales=[240, 360] * 6)
        expected_sigma = pd.Series([240, 360] * 6).std(ddof=1)
        self.assertAlmostEqual(volatile.safety_stock, 1.65 * expected_sigma * math.sqrt(14 / 30))
        self.assertGreater(volatile.safety_stock, stable.safety_stock)
        self.assertGreater(volatile.recommended_order_qty, stable.recommended_order_qty)

    def test_anomaly_does_not_inflate_safety_stock(self):
        row = recommend(sales=[300] * 11 + [100000])
        self.assertEqual(row.safety_stock, 0)
        self.assertEqual(row.recommended_order_qty, 140)
        self.assertEqual(row.anomalies_excluded, 1)
        self.assertEqual(row.status, "REVIEW")
        self.assertIn("sales_anomalies_detected", row.review_reasons)

    def test_insufficient_history_fallback(self):
        row = recommend(sales=[300])
        self.assertEqual(row.monthly_demand_std, 300)
        self.assertAlmostEqual(row.safety_stock, 1.65 * 300 * math.sqrt(14 / 30))
        self.assertEqual(row.status, "REVIEW")
        self.assertIn("Insufficient-history fallback", explain_replenishment(row))

    def test_configurable_service_factor_and_zero_lead(self):
        forecasts, audit = inputs(sales=[240, 360] * 6)
        low = calculate_replenishment(forecasts, audit, 1).iloc[0]
        high = calculate_replenishment(forecasts, audit, 2).iloc[0]
        self.assertAlmostEqual(high.safety_stock, 2 * low.safety_stock)
        self.assertEqual(recommend(lead=0).recommended_order_qty, 0)
        for invalid in [-1, float("inf"), float("nan")]:
            with self.assertRaises(ValueError):
                calculate_replenishment(forecasts, audit, invalid)

    def test_invalid_inventory_requires_review_without_false_zero(self):
        for key in ["current_stock", "in_transit", "lead_time_days", "forecast_demand"]:
            for value in [-1, float("nan"), float("inf")]:
                forecasts, audit = inputs()
                forecasts[key] = forecasts[key].astype(float)
                forecasts.loc[0, key] = value
                row = calculate_replenishment(forecasts, audit).iloc[0]
                self.assertEqual(row.status, "REVIEW")
                self.assertTrue(pd.isna(row.recommended_order_qty))
                self.assertIn(f"invalid_{key}", row.review_reasons)
                self.assertIn("cannot be calculated", explain_replenishment(row))

    def test_history_gaps_and_window(self):
        forecasts, audit = inputs(sales=[1000] * 12 + [300] * 12)
        row = calculate_replenishment(forecasts, audit).iloc[0]
        self.assertEqual(row.history_observations, 12)
        self.assertEqual(row.safety_stock, 0)
        audit = audit.drop(audit.index[-2])
        row = calculate_replenishment(forecasts, audit).iloc[0]
        self.assertIn("missing_history_months", row.review_reasons)

    def test_explanation_contains_calculation_and_zero_reason(self):
        row = recommend(stock=80, transit=20)
        text = explain_replenishment(row)
        self.assertIn(f"recommended replenishment quantity is {row.recommended_order_qty} units", text)
        self.assertIn(f"{row.safety_stock:.2f} units", text)
        self.assertIn("No order is recommended because", explain_replenishment(recommend(stock=1000)))
        row = recommend(sales=([360] + [300] * 11) * 2)
        self.assertNotEqual(row.seasonal_factor, 1)
        self.assertIn(f"seasonal factor of {row.seasonal_factor:.4f}", explain_replenishment(row))

    def test_supplier_totals_and_latest_price(self):
        forecasts_a, audit_a = inputs(sku="A")
        forecasts_b, audit_b = inputs(sku="B", stock=100)
        audit_a.loc[audit_a.index[-1], "unit_price"] = 3.0
        result = calculate_replenishment(pd.concat([forecasts_a, forecasts_b]), pd.concat([audit_a, audit_b]))
        lines, totals = supplier_proposals(result)
        self.assertEqual(len(lines), 2)
        self.assertEqual(totals.iloc[0].total_units, 180)
        self.assertEqual(totals.iloc[0].estimated_order_value, 140 * 3 + 40 * 2.5)
        self.assertEqual(totals.iloc[0].skus_to_order, 2)

    def test_missing_price_does_not_understate_supplier_value(self):
        forecasts, audit = inputs(price=float("nan"))
        result = calculate_replenishment(forecasts, audit)
        self.assertEqual(result.iloc[0].status, "REVIEW")
        self.assertEqual(result.iloc[0].recommended_order_qty, 140)
        lines, totals = supplier_proposals(result)
        self.assertTrue(pd.isna(totals.iloc[0].estimated_order_value))
        self.assertEqual(totals.iloc[0].skus_requiring_review, 1)
        self.assertEqual(len(lines), 1)

    def test_empty_proposals(self):
        result = calculate_replenishment(*inputs(stock=1000))
        lines, totals = supplier_proposals(result)
        self.assertTrue(lines.empty)
        self.assertTrue(totals.empty)

    def test_complete_application(self):
        app = AppTest.from_file(str(ROOT / "app.py"))
        app.session_state["data_source"] = "Synthetic demo"
        app.run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        headings = [item.value for item in app.subheader]
        self.assertIn("Demand Forecast", headings)
        self.assertIn("Replenishment Recommendations", headings)
        tables = [item.value for item in app.dataframe]
        recommendations = next(table for table in tables if "recommended_order_qty" in table and "target_stock" in table)
        self.assertEqual(len(recommendations), 8)
        self.assertGreaterEqual(len(app.get("plotly_chart")), 3)
        app.selectbox(key="selected_sku").select("SNK-440").run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        app.number_input(key="service_factor").set_value(2.0).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        supplier_table = next(item.value for item in app.dataframe if "total_units" in item.value)
        self.assertGreater(supplier_table["total_units"].sum(), 0)
        metric_values = {item.label: item.value for item in app.metric}
        self.assertEqual(int(metric_values["Total units recommended"]), supplier_table["total_units"].sum())
        self.assertTrue(any("recommended replenishment quantity" in item.value for item in app.markdown))
        self.assertEqual([button.label for button in app.button], ["Generate decision brief", "Analyze SKU", "Approve reviewed quantity"])


if __name__ == "__main__":
    unittest.main()
