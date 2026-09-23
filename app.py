"""Streamlit entry point for the Solai replenishment MVP scaffold."""

from pathlib import Path

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

from src.data_loader import load_sample_data
from src.forecasting import explain_forecast, forecast_demand
from src.replenishment import calculate_replenishment, supplier_proposals
from src.explanations import explain_replenishment


st.set_page_config(page_title="Solai | Replenishment", page_icon="📦", layout="wide")
st.title("Warehouse Replenishment")
st.caption(
    "Procurement recommendations will be prepared for manager review. "
    "This demo does not send orders to suppliers."
)

data_path = Path(__file__).parent / "data" / "sample_data.csv"

try:
    data = load_sample_data(data_path)
except (OSError, ValueError) as exc:
    st.error(f"Could not load sample data: {exc}")
else:
    sku_count = data["sku"].nunique()
    supplier_count = data["supplier"].nunique()
    left, middle, right = st.columns(3)
    left.metric("Sales history rows", f"{len(data):,}")
    middle.metric("SKUs", sku_count)
    right.metric("Suppliers", supplier_count)

    st.subheader("Sample sales history and inventory")
    st.dataframe(data, use_container_width=True, hide_index=True)

    st.subheader("Demand Forecast")
    st.caption(
        "Next-month demand in units/month. Baseline uses the latest six calendar "
        "months, falling back to older regular observations if needed. "
        "Missing months are not assumed to have zero sales."
    )
    forecasts, audit = forecast_demand(data)
    display_columns = [
        "sku", "product_name", "category", "supplier", "baseline_demand",
        "seasonal_factor", "forecast_demand", "anomalies_detected",
        "current_stock", "in_transit", "lead_time_days",
    ]
    st.dataframe(forecasts[display_columns].round(2), use_container_width=True, hide_index=True)
    selected_sku = st.selectbox("SKU", forecasts["sku"].tolist())
    selected = forecasts.loc[forecasts["sku"] == selected_sku].iloc[0]
    history = audit.loc[audit["sku"] == selected_sku]
    anomalies = history.loc[history["is_anomaly"]]
    chart = go.Figure()
    chart.add_trace(go.Scatter(
        x=history["date"], y=history["sales"], mode="lines+markers", name="Historical sales",
    ))
    chart.add_trace(go.Scatter(
        x=anomalies["date"], y=anomalies["sales"], mode="markers",
        name="Sales spike", marker=dict(color="red", size=12, symbol="x"),
    ))
    for column, label, dash in [
        ("baseline_demand", "Recent baseline", "dash"),
        ("forecast_demand", "Next-month forecast", "dot"),
    ]:
        chart.add_trace(go.Scatter(
            x=[history["date"].min(), selected["forecast_month"]],
            y=[selected[column], selected[column]], mode="lines", name=label,
            line=dict(dash=dash),
        ))
    chart.update_layout(xaxis_title="Month", yaxis_title="Sales (units/month)")
    st.plotly_chart(chart, use_container_width=True)
    st.write(explain_forecast(selected))
    with st.expander("Forecast inputs and anomaly audit"):
        st.write(f"Raw recent mean (including spikes): {selected['raw_demand_estimate']:.1f} units/month")
        st.dataframe(history, use_container_width=True, hide_index=True)

    st.subheader("Replenishment Recommendations")
    st.caption(
        "Proposals for manager review only. REVIEW takes priority over ORDER or COVERED "
        "when data needs attention; its calculated quantity is provisional. "
        "In-transit stock is assumed to arrive within the lead-time horizon."
    )
    service_factor = st.number_input(
        "Safety-stock service-level factor", min_value=0.0, value=1.65, step=0.05,
        help="Safety stock = factor × monthly demand standard deviation × sqrt(lead-time days / 30). "
             "This is an approximation from monthly data, not a guaranteed service level.",
    )
    recommendations = calculate_replenishment(forecasts, audit, service_factor)
    order_mask = recommendations["recommended_order_qty"].gt(0).fillna(False)
    metrics = st.columns(4)
    metrics[0].metric("SKUs requiring order", int(order_mask.sum()))
    metrics[1].metric("Total units recommended", int(recommendations["recommended_order_qty"].sum()))
    metrics[2].metric("SKUs covered by existing inventory", int(recommendations["status"].eq("COVERED").sum()))
    metrics[3].metric("SKUs requiring review", int(recommendations["status"].eq("REVIEW").sum()))
    st.caption(
        "Order counts and units include provisional positive quantities marked REVIEW. "
        "Covered counts include only COVERED rows. Uncalculable quantities are shown as blank. "
        "Orange rows require an order; yellow rows require review."
    )
    recommendation_columns = [
        "sku", "product_name", "supplier", "forecast_demand", "lead_time_days",
        "lead_time_demand", "safety_stock", "current_stock", "in_transit",
        "inventory_position", "target_stock", "recommended_order_qty", "status",
    ]

    def highlight_recommendation(row):
        color = {"ORDER": "#ffcc80", "REVIEW": "#fff59d"}.get(row["status"])
        return [f"background-color: {color}; color: #111111" if color else ""] * len(row)

    st.dataframe(
        recommendations[recommendation_columns].round(2).style.apply(highlight_recommendation, axis=1),
        use_container_width=True, hide_index=True,
    )

    stock_chart = px.bar(
        recommendations, x="sku", y=["current_stock", "in_transit", "target_stock"],
        barmode="group", title="Inventory and target stock by SKU",
        labels={"value": "Units", "sku": "SKU", "variable": "Stock measure"},
    )
    st.plotly_chart(stock_chart, use_container_width=True)
    quantity_chart = px.bar(
        recommendations, x="sku", y="recommended_order_qty", color="status",
        title="Recommended order quantity by SKU",
        labels={"recommended_order_qty": "Units to order", "sku": "SKU"},
        color_discrete_map={"ORDER": "#e67700", "COVERED": "#2e7d32", "REVIEW": "#a07800"},
    )
    st.plotly_chart(quantity_chart, use_container_width=True)

    st.subheader("Supplier proposals — manager review only")
    proposal_lines, supplier_totals = supplier_proposals(recommendations)
    st.caption(
        "Estimated values use each SKU's latest unit_price, assuming a common currency. "
        "Taxes, shipping, pack sizes and minimum order quantities are not included. "
        "A missing price leaves the supplier total value unknown."
    )
    if supplier_totals.empty:
        st.info("No positive order quantities are currently proposed. Check any REVIEW rows above.")
    else:
        st.dataframe(supplier_totals.round(2), use_container_width=True, hide_index=True)
        for supplier, lines in proposal_lines.groupby("supplier", sort=True):
            with st.expander(f"{supplier} — {int(lines['recommended_order_qty'].sum())} proposed units"):
                st.dataframe(lines.round(2), use_container_width=True, hide_index=True)

    why_sku = st.selectbox("Why this order?", recommendations["sku"].tolist())
    recommendation = recommendations.loc[recommendations["sku"] == why_sku].iloc[0]
    st.write(explain_replenishment(recommendation))
    calculation_columns = [
        "forecast_demand", "daily_demand", "lead_time_days", "lead_time_demand",
        "monthly_demand_std", "service_level_factor", "history_observations",
        "safety_stock", "current_stock", "in_transit", "inventory_position",
        "target_stock", "raw_order_qty", "recommended_order_qty",
    ]
    st.dataframe(
        recommendation[calculation_columns].rename_axis("Calculation").reset_index(name="Value"),
        use_container_width=True, hide_index=True,
    )
