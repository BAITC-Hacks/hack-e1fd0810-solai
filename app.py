"""Streamlit entry point for the Solai replenishment MVP scaffold."""

from pathlib import Path

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

from src.data_loader import load_sample_data
from src.engine import load_engine
from src.translations import t, translated_frame
from src.product_translator import translate_product_name

engine = load_engine()


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
        column: st.column_config.Column(label=t(f"col_{column}"))
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
    data = load_sample_data(data_path)
except (OSError, ValueError) as exc:
    st.error(t("load_error").format(error=exc))
else:
    sku_count = data["sku"].nunique()
    supplier_count = data["supplier"].nunique()
    left, middle, right = st.columns(3)
    left.metric(t("sales_rows"), f"{len(data):,}")
    middle.metric(t("skus"), sku_count)
    right.metric(t("suppliers"), supplier_count)

    st.subheader(t("history_heading"))
    shown_data, column_config = display_frame(data)
    st.dataframe(shown_data, use_container_width=True, hide_index=True, column_config=column_config)

    st.subheader(t("forecast_heading"))
    st.caption(t("forecast_caption"))
    forecasts, audit = engine.forecasting.forecast_demand(data)
    display_columns = [
        "sku", "product_name", "category", "supplier", "baseline_demand",
        "raw_sales_baseline", "stockout_adjustment", "estimated_lost_demand",
        "seasonal_factor", "growth_factor", "forecast_demand", "anomalies_detected",
        "current_stock", "in_transit", "lead_time_days",
    ]
    shown_forecasts, column_config = display_frame(forecasts[display_columns].round(2))
    st.dataframe(shown_forecasts, use_container_width=True, hide_index=True, column_config=column_config)
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
    st.plotly_chart(chart, use_container_width=True)
    st.write(engine.forecasting.explain_forecast(selected, st.session_state.language))
    with st.expander(t("forecast_audit")):
        st.write(t("raw_mean").format(value=selected["raw_demand_estimate"]))
        shown_history, column_config = display_frame(history)
        st.dataframe(shown_history, use_container_width=True, hide_index=True, column_config=column_config)

    st.subheader(t("replenishment_heading"))
    st.caption(t("replenishment_caption"))
    service_factor = st.number_input(
        t("service_factor"), min_value=0.0, value=1.65, step=0.05,
        help=t("service_help"),
    )
    recommendations = engine.replenishment.calculate_replenishment(forecasts, audit, service_factor)
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
        "inventory_position", "target_stock", "recommended_order_qty", "status",
    ]

    def highlight_recommendation(row):
        color = {"ORDER": "#ffcc80", "REVIEW": "#fff59d"}.get(row["status"])
        return [f"background-color: {color}; color: #111111" if color else ""] * len(row)

    display_recommendations = recommendations[recommendation_columns].round(2).copy()
    display_recommendations["product_name"] = display_recommendations["product_name"].map(
        lambda name: translate_product_name(name, st.session_state.language)
    )
    st.dataframe(
        display_recommendations.style.apply(highlight_recommendation, axis=1),
        use_container_width=True, hide_index=True,
        column_config={column: st.column_config.Column(label=t(f"col_{column}")) for column in recommendation_columns},
    )

    stock_chart = px.bar(
        recommendations, x="sku", y=["current_stock", "in_transit", "target_stock"],
        barmode="group", title=t("inventory_chart"),
        labels={"value": t("units"), "sku": t("sku"), "variable": t("stock_measure")},
    )
    stock_chart.for_each_trace(lambda trace: trace.update(name=t(f"stock_{trace.name}")))
    st.plotly_chart(stock_chart, use_container_width=True)
    quantity_chart = px.bar(
        recommendations, x="sku", y="recommended_order_qty", color="status",
        title=t("quantity_chart"),
        labels={"recommended_order_qty": t("units_to_order"), "sku": t("sku")},
        color_discrete_map={"ORDER": "#e67700", "COVERED": "#2e7d32", "REVIEW": "#a07800"},
    )
    quantity_chart.for_each_trace(lambda trace: trace.update(name=t(f"status_{trace.name}")))
    st.plotly_chart(quantity_chart, use_container_width=True)

    st.subheader(t("supplier_heading"))
    proposal_lines, supplier_totals = engine.replenishment.supplier_proposals(recommendations)
    st.caption(t("supplier_caption"))
    if supplier_totals.empty:
        st.info(t("no_proposals"))
    else:
        shown_totals, column_config = display_frame(supplier_totals.round(2))
        st.dataframe(shown_totals, use_container_width=True, hide_index=True, column_config=column_config)
        for supplier, lines in proposal_lines.groupby("supplier", sort=True):
            with st.expander(t("proposed_units").format(supplier=supplier, quantity=int(lines["recommended_order_qty"].sum()))):
                display_lines = lines.round(2).copy()
                display_lines["status"] = display_lines["status"].map(lambda value: t(f"status_{value}"))
                shown_lines, column_config = display_frame(display_lines)
                st.dataframe(shown_lines, use_container_width=True, hide_index=True, column_config=column_config)

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
        "target_stock", "raw_order_qty", "recommended_order_qty",
    ]
    calculation_frame = recommendation[calculation_columns].rename_axis(t("calculation")).reset_index(name=t("value"))
    calculation_frame.columns = [t("calculation"), t("value")]
    st.dataframe(calculation_frame, use_container_width=True, hide_index=True)
    st.caption(
        "Forecast calculation: raw recent mean + robust/spike adjustment + stockout adjustment "
        "+ seasonal adjustment + growth adjustment = final monthly forecast. "
        "The robust/spike adjustment includes changing from a mean to a median; it is not solely lost spike sales. "
        "Estimated lost demand in the forecast table is a historical total, not an extra amount to order."
    )
