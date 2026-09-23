"""Real workbook integration and conservative normalization boundaries."""

from pathlib import Path
import unittest

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest

from src.partner_loader import (load_partner_data, normalize_monthly, detect_document_anomalies,
                                infer_stockout_evidence, reconcile_document_sales)
from src.partner_pipeline import partner_forecasts
from src.forecasting import forecast_demand
from src.replenishment import calculate_replenishment, apply_order_constraints

ROOT = Path(__file__).resolve().parents[1]


def documents(quantities):
    return pd.DataFrame({"brand": "IEK", "sku": "0001_", "date": pd.Timestamp("2026-08-01"),
                         "quantity": quantities, "document_id": [str(i) for i in range(len(quantities))],
                         "warehouse": "A", "document_type": "Расходная накладная"})


class PartnerUnitTests(unittest.TestCase):
    def test_monthly_schema_preserves_keys_blanks_and_signed_returns(self):
        frame = pd.DataFrame({"Код": ["0001_", "002"], "Товар": ["A", "B"],
                              "Январь 2026": [10, None], "Февраль 2026": [-2, 0], "Итого": [8, 0]})
        actual = normalize_monthly(frame, "Код", "Товар", "sales")
        self.assertEqual(len(actual), 4)
        self.assertEqual(set(actual.sku), {"0001_", "002"})
        self.assertEqual(actual.sales.isna().sum(), 1)
        self.assertIn(-2, actual.sales.values)
        with self.assertRaises(ValueError):
            normalize_monthly(pd.concat([frame, frame]), "Код", "Товар", "sales")

    def test_one_off_document_spike_is_retained_and_subtracted_only_after_reconciliation(self):
        docs = detect_document_anomalies(documents([10] * 9 + [1000]))
        self.assertEqual(len(docs), 10)
        self.assertEqual(docs.is_document_anomaly.sum(), 1)
        self.assertEqual(docs.iloc[-1].quantity, 1000)
        self.assertEqual(docs.iloc[-1].document_anomaly_reason, "isolated_large_document")
        monthly = pd.DataFrame({"sku": ["0001_"], "date": [pd.Timestamp("2026-08-01")], "raw_net_sales": [1090]})
        actual = reconcile_document_sales(monthly, docs).iloc[0]
        self.assertEqual(actual.raw_sales, 1090)
        self.assertEqual(actual.sales, 90)
        monthly["raw_net_sales"] = 2000
        mismatch = reconcile_document_sales(monthly, docs).iloc[0]
        self.assertEqual(mismatch.sales, 2000)
        self.assertEqual(mismatch.document_anomalies_unreconciled, 1)

    def test_document_lines_aggregate_and_repeated_bulk_is_not_one_off(self):
        docs = documents([10] * 9 + [500, 500])
        docs.loc[10, "document_id"] = "9"
        docs["customer_id"] = "anonymous-example"
        result = detect_document_anomalies(docs)
        self.assertEqual(len(result), 10)
        spike = result.loc[result.is_document_anomaly].iloc[0]
        self.assertEqual(spike.quantity, 1000)
        self.assertEqual(spike.source_line_count, 2)
        self.assertEqual(spike.source_quantities, [500, 500])
        self.assertEqual(spike.customer_id, "anonymous-example")
        repeated = detect_document_anomalies(documents([10] * 9 + [1000, 1000]))
        self.assertFalse(repeated.is_document_anomaly.any())

    def test_missing_monthly_value_uses_only_observed_documents(self):
        monthly = pd.DataFrame({"sku": ["0001_", "unknown"], "date": pd.Timestamp("2026-08-01"),
                                "raw_net_sales": [np.nan, np.nan]})
        actual = reconcile_document_sales(monthly, detect_document_anomalies(documents([10] * 8)))
        self.assertEqual(actual.iloc[0].sales, 80)
        self.assertTrue(pd.isna(actual.iloc[1].sales))

    def test_missing_document_quantity_does_not_become_a_complete_month(self):
        docs = detect_document_anomalies(documents([10] * 8 + [np.nan]))
        monthly = pd.DataFrame({"sku": ["0001_"], "date": [pd.Timestamp("2026-08-01")], "raw_net_sales": [np.nan]})
        row = reconcile_document_sales(monthly, docs).iloc[0]
        self.assertEqual(row.observed_document_net, 80)
        self.assertEqual(row.document_missing_quantities, 1)
        self.assertTrue(pd.isna(row.sales))
        self.assertFalse(row.document_reconciled)

    def test_moq_rounding_minimum_and_no_order(self):
        self.assertEqual(apply_order_constraints(137, order_multiple=20), 140)
        self.assertEqual(apply_order_constraints(21, minimum_order_qty=20), 21)
        self.assertEqual(apply_order_constraints(3, minimum_order_qty=20), 20)
        self.assertEqual(apply_order_constraints(3, minimum_order_qty=25, order_multiple=20), 40)
        self.assertEqual(apply_order_constraints(0, 25, 20), 0)
        self.assertIsNone(apply_order_constraints(None, 25, 20))

    def test_zero_or_missing_moq_preserves_requirement(self):
        for constraint in [None, 0, np.nan]:
            self.assertEqual(apply_order_constraints(137, constraint, constraint), 137)

    def test_stockout_requires_inventory_evidence_and_never_invents_days(self):
        data = pd.DataFrame({"sku": "A", "product_name": "A", "category": None, "supplier": "IEK",
                             "date": pd.date_range("2026-01-01", periods=6, freq="MS"),
                             "sales": [310, 280, 310, 100, 100, 100],
                             "historical_stock": [50, 50, 50, np.nan, np.nan, np.nan],
                             "current_stock": 0, "in_transit": 0, "lead_time_days": 30})
        raw, _ = forecast_demand(infer_stockout_evidence(data))
        self.assertEqual(infer_stockout_evidence(data).possible_stockout.sum(), 0)
        data.loc[3:, "historical_stock"] = 0
        evidence = infer_stockout_evidence(data)
        self.assertTrue(evidence.stockout_days.isna().all())
        adjusted, audit = forecast_demand(evidence)
        self.assertGreater(adjusted.iloc[0].forecast_demand, raw.iloc[0].forecast_demand)
        self.assertTrue(audit.iloc[3:].stockout_method.eq("inferred_zero_inventory_snapshot").all())


class RealPartnerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.partner = load_partner_data(ROOT / "data/raw")

    def test_all_workbooks_counts_and_missing_optional_fields(self):
        d = self.partner
        self.assertEqual(len({r["filename"] for r in d.inspection}), 12)
        self.assertEqual(len(d.inspection), 14)
        self.assertEqual(d.catalog.groupby("brand").size().to_dict(), {"IEK": 3185, "Systeme Electric": 724})
        self.assertFalse(d.catalog.sku.duplicated().any())
        self.assertTrue(d.catalog.lead_time_days.isna().all())
        self.assertTrue(d.catalog.unit_price.isna().all())
        self.assertNotIn("customer_id", d.documents)
        self.assertFalse(d.history.possible_stockout.any())
        self.assertEqual(d.history.date.min(), pd.Timestamp("2024-01-01"))
        self.assertEqual(d.history.date.max(), pd.Timestamp("2026-09-01"))

    def test_sku_joins_and_real_pipeline(self):
        d = self.partner
        totals = d.inbound.groupby("sku").quantity.sum(min_count=1)
        catalog = d.catalog.set_index("sku")
        pd.testing.assert_series_equal(catalog.loc[totals.index, "in_transit"], totals, check_names=False, check_dtype=False)
        selected = d.catalog.loc[d.catalog.brand.eq("Systeme Electric") & d.catalog.in_transit.notna()
                                 & d.catalog.current_stock.notna()].sku.head(50)
        forecasts, audit = partner_forecasts(d, selected)
        unplanned = calculate_replenishment(forecasts, audit)
        self.assertTrue(unplanned.recommended_order_qty.isna().all())
        forecasts, audit = partner_forecasts(d, selected, planning_lead_days=60)
        planned = calculate_replenishment(forecasts, audit)
        self.assertTrue(planned.recommended_order_qty.gt(0).any())
        self.assertTrue(planned.status.eq("REVIEW").all())
        self.assertTrue(forecasts.seasonal_source.isin(["partner_brand", "none"]).all())
        self.assertTrue(audit.date.lt(pd.Timestamp("2026-09-01")).all())
        for _, row in planned.loc[planned.recommended_order_qty.notna()].iterrows():
            self.assertGreaterEqual(row.recommended_order_qty, row.raw_required_qty)
            if row.recommended_order_qty and row.order_multiple:
                self.assertEqual(row.recommended_order_qty % row.order_multiple, 0)
        self.assertTrue({"raw_sales_baseline", "stockout_adjustment", "growth_factor", "estimated_lost_demand"}.issubset(forecasts.columns))

    def test_entire_catalog_produces_auditable_recommendations(self):
        forecasts, audit = partner_forecasts(self.partner, planning_lead_days=30)
        recommendations = calculate_replenishment(forecasts, audit)
        self.assertEqual(len(recommendations), 3909)
        self.assertFalse(recommendations.sku.duplicated().any())
        self.assertTrue(recommendations.recommended_order_qty.dropna().ge(0).all())
        self.assertEqual(set(recommendations.sku), set(self.partner.catalog.sku))
        for brand in ["IEK", "Systeme Electric"]:
            self.assertTrue(recommendations.loc[recommendations.brand.eq(brand)].recommended_order_qty.notna().any())

    def test_streamlit_partner_render_edit_approve_and_pagination(self):
        app = AppTest.from_file(str(ROOT / "app.py"))
        app.run(timeout=120)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.sidebar.selectbox[0].value, "Partner data")
        self.assertEqual(len(app.dataframe[1].value), 25)
        brand = next(w for w in app.sidebar.selectbox if w.label == "Supplier / brand")
        brand.select("Systeme Electric").run(timeout=60)
        next(w for w in app.sidebar.number_input if w.label == "Planning lead time (days)").set_value(60).run(timeout=60)
        self.assertEqual(len(app.exception), 0)
        table = next(t.value for t in app.dataframe if "raw_required_qty" in t.value and "urgency" in t.value)
        self.assertTrue(table.recommended_order_qty.notna().any())
        sku = app.selectbox[1].value
        original = table.set_index("sku").loc[sku, "recommended_order_qty"]
        next(w for w in app.number_input if w.label == "Manager order quantity").set_value(20).run(timeout=60)
        next(w for w in app.button if w.label == "Approve reviewed quantity").click().run(timeout=60)
        self.assertEqual(len(app.exception), 0)
        def reviewed():
            return next(t.value for t in app.dataframe if "decision_state" in t.value and "quantity_changed" in t.value)
        row = reviewed().set_index("sku").loc[sku]
        self.assertEqual(row.manager_order_qty, 20)
        self.assertEqual(row.decision_state, "Approved")
        self.assertTrue(pd.isna(row.recommended_order_qty) if pd.isna(original) else row.recommended_order_qty == original)
        page = next(w for w in app.sidebar.selectbox if w.label == "Results page (25 SKUs)")
        page.select(2).run(timeout=60)
        next(w for w in app.sidebar.selectbox if w.label == "Results page (25 SKUs)").select(1).run(timeout=60)
        self.assertEqual(reviewed().set_index("sku").loc[sku, "decision_state"], "Approved")
        self.assertTrue(any("No supplier order was sent" in item.value for item in app.success))
        self.assertTrue({"raw_sales_baseline", "stockout_adjustment", "growth_factor", "estimated_lost_demand"}.issubset(app.dataframe[1].value.columns))


if __name__ == "__main__":
    unittest.main()
