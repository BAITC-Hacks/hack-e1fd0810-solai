"""Streamlit entry point for the Solai replenishment MVP scaffold."""

from pathlib import Path
import importlib

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

from src.data_loader import load_sample_data
from src.translations import t
from src.product_translator import translate_product_name
from src.partner_loader import load_partner_data, workbook_fingerprint
from src.partner_pipeline import partner_forecasts


@st.cache_data(show_spinner="Reading and normalizing partner workbooks...")
def cached_partner_data(raw_dir, fingerprint):
    # Refresh adapter code on a cache miss in an already-running Streamlit process.
    adapter = importlib.reload(importlib.import_module("src.partner_loader"))
    return adapter.load_partner_data(raw_dir)


@st.cache_data(show_spinner="Calculating partner forecasts...")
def cached_partner_forecasts(raw_dir, fingerprint, skus, lead_days):
    partner = cached_partner_data(raw_dir, fingerprint)
    return partner_forecasts(partner, skus, lead_days)

from src import engine as engine_loader


if getattr(engine_loader, "EXPECTED_RECOMMENDATION_SCHEMA_VERSION", None) != 4:
    engine_loader = importlib.reload(engine_loader)
engine = engine_loader.load_engine()


def save_manager_quantity(sku, widget_key):
    """Persist edits independently of the selected SKU's widget lifetime."""
    quantity = st.session_state[widget_key]
    if quantity is not None:
        st.session_state["manager_reviews"] = engine.manager_review.set_review_quantity(
            st.session_state["manager_reviews"], sku, quantity,
        )


def display_frame(frame):
    """Localize visible labels and product names on a display-only copy."""
    result = frame.copy()
    if "product_name" in result.columns:
        names = result["product_name"].dropna().unique()
        localized = {
            name: translate_product_name(name, st.session_state.language)
            for name in names
        }
        result["product_name"] = result["product_name"].map(
            lambda name: localized.get(name, name)
        )
    columns = {
        column: st.column_config.Column(label=(t(f"col_{column}") if t(f"col_{column}") != f"col_{column}" else column.replace("_", " ").title()))
        for column in result.columns
    }
    return result, columns


if "language" not in st.session_state:
    st.session_state.language = "en"
st.set_page_config(page_title=t("page_title"), page_icon="📦", layout="wide")
_, language_col = st.columns([8, 1])
with language_col:
    st.radio(t("language"), ["EN", "RU", "KZ"], key="language_choice",
             index=["en", "ru", "kz"].index(st.session_state.language),
             on_change=lambda: setattr(st.session_state, "language", st.session_state.language_choice.lower()),
             horizontal=True, label_visibility="collapsed")
st.title(t("title"))
st.caption(t("intro"))

data_path = Path(__file__).parent / "data" / "sample_data.csv"

try:
    raw_dir = str(data_path.parent / "raw")
    fingerprint = workbook_fingerprint(raw_dir)
    if fingerprint:
        fingerprint = (*fingerprint, ("adapter_code", (Path(__file__).parent / "src" / "partner_loader.py").stat().st_mtime_ns))
    mode = st.sidebar.selectbox("Data source", ["Partner data", "Synthetic demo"],
                                index=0 if fingerprint else 1, key="data_source")
    is_partner = mode == "Partner data"
    if is_partner:
        partner = cached_partner_data(raw_dir, fingerprint)
        st.info(f"Partner-provided data: {len(partner.catalog):,} real SKUs. Snapshot: {partner.as_of:%Y-%m-%d}. "
                "Missing lead time, stock and transit remain unknown. No orders are sent.")
        brand = st.sidebar.selectbox("Supplier / brand", sorted(partner.catalog.brand.unique()))
        search = st.sidebar.text_input("Search real SKU or product name")
        catalog = partner.catalog.loc[partner.catalog.brand.eq(brand)]
        if search:
            catalog = catalog.loc[catalog.sku.str.contains(search, case=False, regex=False)
                                  | catalog.product_name.str.contains(search, case=False, regex=False, na=False)]
        catalog = catalog.sort_values("sku")
        if catalog.empty:
            st.info("No matching products.")
            st.stop()
        page_count = (len(catalog) + 24) // 25
        page = st.sidebar.selectbox("Results page (25 SKUs)", range(1, page_count + 1))
        page_skus = tuple(catalog.iloc[(page - 1) * 25:page * 25].sku)
        lead_days = st.sidebar.number_input("Planning lead time (days)", min_value=0, value=None, step=1,
            help="Absent from partner files. Enter an explicit manager assumption to calculate provisional quantities.")
        st.caption(f"Showing page {page}/{page_count} of {len(catalog):,} matching SKUs. "
                   "Metrics, charts, supplier totals and export cover this page only. "
                   "Inventory reports are not consistently warehouse-allocated; filtering uses brand. "
                   "The partial September month is retained in audit but excluded from model fitting.")
        forecasts, audit = cached_partner_forecasts(raw_dir, fingerprint, page_skus, lead_days)
        data = partner.history.loc[partner.history.sku.isin(page_skus)].merge(
            partner.catalog[["sku", "supplier"]], on="sku", validate="many_to_one")
    else:
        data = load_sample_data(data_path)
        forecasts, audit = engine.forecasting.forecast_demand(data)
except (OSError, ValueError) as exc:
    st.error(f"Could not load selected partner data: {exc}" if is_partner else t("load_error").format(error=exc))
else:
    sku_count = data["sku"].nunique()
    supplier_count = data["supplier"].nunique()
    left, middle, right = st.columns(3)
    left.metric(t("sales_rows"), f"{len(data):,}")
    middle.metric(t("skus"), sku_count)
    right.metric(t("suppliers"), supplier_count)

    st.subheader("Partner sales history and inventory" if is_partner else t("history_heading"))
    shown_data, column_config = display_frame(data.head(200))
    st.dataframe(shown_data, width="stretch", hide_index=True, column_config=column_config)

    st.subheader(t("forecast_heading"))
    st.caption(t("forecast_caption"))
    if is_partner:
        st.caption("History preview: first 200 rows. Complete selected-SKU history and documents appear in the audit below.")
    display_columns = [
        "sku", "product_name", "category", "supplier", "baseline_demand",
        "raw_sales_baseline", "stockout_adjustment", "estimated_lost_demand",
        "seasonal_factor", "growth_factor", "forecast_demand", "anomalies_detected",
        "current_stock", "in_transit", "lead_time_days",
    ]
    shown_forecasts, column_config = display_frame(forecasts[display_columns].round(2))
    st.dataframe(shown_forecasts, width="stretch", hide_index=True, column_config=column_config)
    sku_products = forecasts.set_index("sku")["product_name"].to_dict()
    selected_sku = st.selectbox(t("sku"), forecasts["sku"].tolist())
    st.caption(translate_product_name(sku_products[selected_sku], st.session_state.language))
    selected = forecasts.loc[forecasts["sku"] == selected_sku].iloc[0]
    history = audit.loc[audit["sku"] == selected_sku]
    anomalies = history.loc[history["is_anomaly"]]
    chart = go.Figure()
    chart.add_trace(go.Scatter(
        x=history["date"], y=history["sales"], mode="lines+markers", name=t("historical_sales"),
    ))
    chart.add_trace(go.Scatter(
        x=anomalies["date"], y=anomalies["sales"], mode="markers",
        name=t("sales_spike"), marker=dict(color="red", size=12, symbol="x"),
    ))
    chart.add_trace(go.Scatter(
        x=history["date"], y=history["adjusted_demand"].where(~history["is_anomaly"]),
        mode="lines+markers", name="Demand including estimated lost sales",
        line=dict(dash="dash"),
    ))
    stockout_history = history.loc[history["is_stockout"]]
    chart.add_trace(go.Scatter(
        x=stockout_history["date"], y=stockout_history["sales"], mode="markers",
        name="Stockout sales", marker=dict(color="orange", size=12, symbol="diamond"),
    ))
    for column, label, dash in [
        ("baseline_demand", t("recent_baseline"), "dash"),
        ("forecast_demand", t("next_forecast"), "dot"),
    ]:
        chart.add_trace(go.Scatter(
            x=[history["date"].min(), selected["forecast_month"]],
            y=[selected[column], selected[column]], mode="lines", name=label,
            line=dict(dash=dash),
        ))
    chart.update_layout(xaxis_title=t("month"), yaxis_title=t("sales_units_month"))
    st.plotly_chart(chart, width="stretch")
    st.write(engine.forecasting.explain_forecast(selected, st.session_state.language))
    with st.expander(t("forecast_audit")):
        st.write(t("raw_mean").format(value=selected["raw_demand_estimate"]))
        shown_history, column_config = display_frame(history)
        st.dataframe(shown_history, width="stretch", hide_index=True, column_config=column_config)
        if is_partner:
            st.caption("Raw monthly cells, partial months, inventory evidence and document reconciliation:")
            st.dataframe(partner.history.loc[partner.history.sku.eq(selected_sku)], hide_index=True)
            documents = partner.documents.loc[partner.documents.sku.eq(selected_sku)].sort_values(
                ["is_document_anomaly", "date"], ascending=[False, False])
            st.caption(f"{len(documents):,} retained documents; preview shows up to 100 with anomaly flags first. "
                       "No customer ID was provided; these are document-level flags.")
            st.dataframe(documents.head(100), hide_index=True)
            st.download_button("Export selected SKU document audit", documents.to_csv(index=False),
                               file_name=f"{selected_sku}_documents.csv", mime="text/csv")

    st.subheader(t("replenishment_heading"))
    st.caption(t("replenishment_caption"))
    service_factor = st.number_input(
        t("service_factor"), min_value=0.0, value=1.65, step=0.05,
        help=t("service_help"),
    )
    recommendations = engine.replenishment.calculate_replenishment(forecasts, audit, service_factor)
    recommendations["explanation"] = recommendations.apply(engine.explanations.explain_replenishment, axis=1)
    st.session_state["manager_reviews"] = engine.manager_review.sync_review_state(
        recommendations, st.session_state.get("manager_reviews", {}),
    )
    order_mask = recommendations["recommended_order_qty"].gt(0).fillna(False)
    metrics = st.columns(4)
    metrics[0].metric(t("skus_order"), int(order_mask.sum()))
    metrics[1].metric(t("units_recommended"), int(recommendations["recommended_order_qty"].sum()))
    metrics[2].metric(t("skus_covered"), int(recommendations["status"].eq("COVERED").sum()))
    metrics[3].metric(t("skus_review"), int(recommendations["status"].eq("REVIEW").sum()))
    st.caption(t("count_caption"))
    recommendation_columns = [
        "sku", "product_name", "supplier", "forecast_demand", "lead_time_days",
        "lead_time_demand", "safety_stock", "current_stock", "in_transit",
        "inventory_position", "target_stock", "raw_required_qty", "minimum_order_qty", "order_multiple",
        "moq_status", "recommended_order_qty", "status", "urgency", "explanation",
    ]

    def highlight_recommendation(row):
        color = {"ORDER": "#ffcc80", "REVIEW": "#fff59d"}.get(row["status"])
        styles = [f"background-color: {color}; color: #111111" if color else ""] * len(row)
        urgency_color = {"CRITICAL": "#ef9a9a", "HIGH": "#ffcc80", "MEDIUM": "#fff59d", "LOW": "#c8e6c9"}[row["urgency"]]
        styles[row.index.get_loc("urgency")] = f"background-color: {urgency_color}; color: #111111; font-weight: bold"
        return styles

    display_recommendations = recommendations[recommendation_columns].round(2).copy()
    display_recommendations["product_name"] = display_recommendations["product_name"].map(
        lambda name: translate_product_name(name, st.session_state.language)
    )
    st.dataframe(
        display_recommendations.style.apply(highlight_recommendation, axis=1),
        width="stretch", hide_index=True,
        column_config={column: st.column_config.Column(label=(t(f"col_{column}") if t(f"col_{column}") != f"col_{column}" else column.replace("_", " ").title())) for column in recommendation_columns},
    )

    stock_plot = recommendations.copy()
    for column in ["current_stock", "in_transit", "target_stock"]:
        stock_plot[column] = pd.to_numeric(stock_plot[column], errors="coerce").astype(float)
    stock_chart = px.bar(
        stock_plot, x="sku", y=["current_stock", "in_transit", "target_stock"],
        barmode="group", title=t("inventory_chart"),
        labels={"value": t("units"), "sku": t("sku"), "variable": t("stock_measure")},
    )
    stock_chart.for_each_trace(lambda trace: trace.update(name=t(f"stock_{trace.name}")))
    st.plotly_chart(stock_chart, width="stretch")
    quantity_chart = px.bar(
        recommendations, x="sku", y="recommended_order_qty", color="status",
        title=t("quantity_chart"),
        labels={"recommended_order_qty": t("units_to_order"), "sku": t("sku")},
        color_discrete_map={"ORDER": "#e67700", "COVERED": "#2e7d32", "REVIEW": "#a07800"},
    )
    quantity_chart.for_each_trace(lambda trace: trace.update(name=t(f"status_{trace.name}")))
    st.plotly_chart(quantity_chart, width="stretch")


    why_sku = st.selectbox(t("why_order"), recommendations["sku"].tolist())
    recommendation = recommendations.loc[recommendations["sku"] == why_sku].iloc[0]
    displayed_product_name = translate_product_name(
        recommendation["product_name"], st.session_state.language
    )
    st.write(
        t("selected_product").format(name=displayed_product_name)
        + engine.explanations.explain_replenishment(recommendation, st.session_state.language)
    )
    calculation_columns = [
        "raw_demand_estimate", "anomaly_adjustment", "raw_sales_baseline",
        "stockout_adjustment", "baseline_demand", "seasonal_factor", "seasonal_adjustment",
        "growth_factor", "growth_adjustment",
        "forecast_demand", "daily_demand", "lead_time_days", "lead_time_demand",
        "monthly_demand_std", "service_level_factor", "history_observations",
        "safety_stock", "current_stock", "in_transit", "inventory_position",
        "target_stock", "raw_order_qty", "raw_required_qty", "minimum_order_qty", "order_multiple", "recommended_order_qty",
        "current_coverage_days", "position_coverage_days",
    ]
    calculation_frame = recommendation[calculation_columns].rename_axis(t("calculation")).reset_index(name=t("value"))
    calculation_frame.columns = [t("calculation"), t("value")]
    st.dataframe(calculation_frame, width="stretch", hide_index=True)
    st.caption(
        "Forecast calculation: raw recent mean + robust/spike adjustment + stockout adjustment "
        "+ seasonal adjustment + growth adjustment = final monthly forecast. "
        "The robust/spike adjustment includes changing from a mean to a median; it is not solely lost spike sales. "
        "Estimated lost demand in the forecast table is a historical total, not an extra amount to order."
    )

    st.subheader("Manager review and approval")
    st.caption(
        "Review the SKU selected in 'Why this order?' above. Calculated quantities remain read-only. "
        "Approval records a demo decision for this SKU only and sends nothing to suppliers. "
        "Edits and decisions survive ordinary reruns in this browser session, but are not saved to a database."
    )
    record = st.session_state["manager_reviews"][why_sku]
    original_quantity = record["original_order_qty"]
    st.write(f"**{why_sku} — calculated recommendation (read-only): "
             f"{original_quantity if original_quantity is not None else 'unavailable'} units.**")
    if record["calculation_changed"]:
        st.warning("Calculation inputs changed. Review the updated explanation and approve again; manual overrides were retained.")
    if recommendation["status"] == "REVIEW":
        st.warning(f"Review these data issues before approving: {recommendation['review_reasons'].replace('_', ' ')}.")
    quantity_key = f"manager_quantity_{why_sku}"
    # The separate review model survives Streamlit deleting unrendered widgets.
    st.session_state[quantity_key] = record["manager_order_qty"]
    st.number_input(
        "Manager order quantity", min_value=0, step=1, key=quantity_key,
        on_change=save_manager_quantity, args=(why_sku, quantity_key),
        help="Your reviewed quantity; the algorithm's original recommendation is never overwritten.",
    )
    if record["manager_order_qty"] is not None:
        constrained = engine.replenishment.apply_order_constraints(record["manager_order_qty"],
            recommendation.get("minimum_order_qty"), recommendation.get("order_multiple"))
        if constrained != record["manager_order_qty"]:
            st.warning(f"Manager quantity does not meet the supplied order constraints; compliant quantity would be {constrained}. Approval remains a demo decision.")
    if record["manager_order_qty"] != original_quantity:
        st.info(f"Manager changed the quantity: calculated {original_quantity}, reviewed {record['manager_order_qty']}.")
    if st.button(
        "Approve reviewed quantity", key=f"approve_{why_sku}",
        disabled=record["manager_order_qty"] is None or record["decision_state"] == "Approved",
    ):
        st.session_state["manager_reviews"] = engine.manager_review.approve_review(
            st.session_state["manager_reviews"], why_sku,
        )
        record = st.session_state["manager_reviews"][why_sku]
    if record["decision_state"] == "Approved":
        st.success(f"Approved (demo only): {record['approved_quantity']} units at {record['approved_at']}. No supplier order was sent.")
    else:
        st.write("Decision state: **Pending**")
    reviewed = engine.manager_review.reviewed_recommendations(recommendations, st.session_state["manager_reviews"])
    st.dataframe(reviewed[[*recommendation_columns, "manager_order_qty",
        "quantity_changed", "decision_state", "approved_at",
    ]], width="stretch", hide_index=True)
    st.download_button("Export reviewed recommendations (current page)", reviewed.to_csv(index=False),
                       file_name="reviewed_recommendations.csv", mime="text/csv")
    with st.expander("Decision history for selected SKU"):
        if record["history"]:
            st.dataframe(record["history"], width="stretch", hide_index=True)
        else:
            st.caption("No manager decision recorded yet.")

    st.subheader(t("supplier_heading"))
    proposal_lines, supplier_totals = engine.replenishment.supplier_proposals(reviewed, "manager_order_qty")
    st.caption(
        "Supplier totals use manager-reviewed quantities, including Pending proposals. "
        "The summary metrics and charts above retain the algorithm's calculated quantities. "
        "Estimated values require unit_price; partner purchase prices are unavailable. "
        "Calculated quantities include supplied minimums/multiples. Taxes and shipping are excluded. "
        "Missing prices leave the total value unknown. "
        "Use a table's download toolbar to export its displayed columns as CSV."
    )
    if supplier_totals.empty:
        st.info("No positive reviewed quantities are currently proposed. Zero-quantity decisions remain visible in the review table.")
    else:
        shown_totals, column_config = display_frame(supplier_totals.round(2))
        st.dataframe(shown_totals, width="stretch", hide_index=True, column_config=column_config)
        for supplier, lines in proposal_lines.groupby("supplier", sort=True):
            with st.expander(t("proposed_units").format(supplier=supplier, quantity=int(lines["manager_order_qty"].sum()))):
                display_lines = lines.round(2).copy()
                display_lines["status"] = display_lines["status"].map(lambda value: t(f"status_{value}"))
                shown_lines, column_config = display_frame(display_lines)
                st.dataframe(shown_lines, width="stretch", hide_index=True, column_config=column_config)
