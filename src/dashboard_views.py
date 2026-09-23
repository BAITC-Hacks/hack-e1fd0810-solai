"""Focused Streamlit views over existing calculation and manager-review outputs."""

import re

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src import decision_intelligence as intelligence
from src.copilot import brief_sections, llm_available, llm_brief, risk_label
from src.engine import load_engine
from src.presentation import PALETTE, clean_text, metric, show_table, style_chart, text
from src.product_translator import translate_product_name

DECISION_COLUMNS = ["sku", "product_name", "supplier", "current_stock", "forecast_demand", "recommended_order_qty", "urgency", "status"]
FORECAST_COLUMNS = ["sku", "product_name", "category", "supplier", "baseline_demand", "raw_sales_baseline",
                    "stockout_adjustment", "estimated_lost_demand", "seasonal_factor", "growth_factor",
                    "forecast_demand", "anomalies_detected", "current_stock", "in_transit", "lead_time_days"]
CALCULATION_COLUMNS = ["raw_demand_estimate", "anomaly_adjustment", "raw_sales_baseline", "stockout_adjustment",
    "baseline_demand", "seasonal_factor", "seasonal_adjustment", "growth_factor", "growth_adjustment",
    "forecast_demand", "daily_demand", "lead_time_days", "lead_time_demand", "monthly_demand_std", "service_level_factor",
    "history_observations", "safety_stock", "current_stock", "in_transit", "inventory_position", "target_stock",
    "raw_order_qty", "raw_required_qty", "minimum_order_qty", "order_multiple", "recommended_order_qty",
    "current_coverage_days", "position_coverage_days"]


def signals(row, history):
    values = intelligence.key_signals(row, history)
    if not values:
        st.caption(text("no_signals"))
    for signal in values:
        key = "history_signal" if signal["code"] == "history" else signal["code"]
        data = {k: intelligence.format_number(v, text("unavailable")) for k, v in signal.items() if k != "code"}
        st.write(text(key).format(**data))


def overview(recommendations, audit):
    summary = intelligence.portfolio_metrics(recommendations)
    columns = st.columns(6)
    labels = ["active", "action", "urgent", "units_recommended", "affected", "skus_review"]
    fields = ["skus", "requiring_action", "critical_high", "recommended_units", "suppliers_affected", "review_count"]
    for column, label, field in zip(columns, labels, fields):
        with column:
            metric(text(label), summary[field])
    if summary["unknown_orders"]:
        st.caption(text("unknown_orders").format(n=summary["unknown_orders"]))
    queue = intelligence.priority_queue(recommendations)
    left, right = st.columns([1.6, 1])
    with left, st.container(border=True):
        st.subheader(text("priority"))
        first = queue.iloc[0]
        action = intelligence.sku_analysis(first)["next_action"]
        st.write(f"**{first['sku']} · {first['urgency']}**")
        st.write(text(action))
        st.caption(clean_text(first.get("urgency_reason", "")))
        show_table(queue[["sku", "urgency", "current_coverage_days", "recommended_order_qty", "status"]].head(5), urgency=True)
        with st.expander(text("priority_rule").split(".")[0]):
            st.write(text("priority_rule"))
            show_table(queue[["sku", "priority_reason"]].head(10))
    with right, st.container(border=True):
        st.subheader(text("quality"))
        risks = intelligence.data_quality_summary(recommendations)
        if not risks:
            st.write(text("no_risks"))
        for code, count in list(risks.items())[:5]:
            st.write(f"**{count:,} SKU** · {risk_label(code, st.session_state.language)}")
        if len(risks) > 5:
            with st.expander(text("data_risks")):
                for code, count in list(risks.items())[5:]:
                    st.write(f"{count:,} SKU · {risk_label(code, st.session_state.language)}")
        st.caption(text("quality_rule"))
    left, right = st.columns(2)
    with left:
        counts = recommendations.urgency.value_counts().reindex(list(PALETTE), fill_value=0).rename_axis("urgency").reset_index(name="skus")
        fig = px.bar(counts, x="urgency", y="skus", color="urgency", color_discrete_map=PALETTE,
                     title=text("urgency_distribution"), labels={"skus": "SKU", "urgency": text("urgency_filter")})
        fig.update_layout(showlegend=False)
        st.plotly_chart(style_chart(fig), width="stretch", key="overview_urgency")
    with right:
        coverage = queue.loc[intelligence.numeric(queue, "current_coverage_days").notna()
                             & intelligence.numeric(queue, "lead_time_days").notna()].head(8)
        if coverage.empty:
            st.subheader(text("coverage"))
            st.caption(text("unavailable"))
            st.write(text("confirm_missing_inputs"))
        else:
            fig = px.bar(coverage, y="sku", x=["current_coverage_days", "lead_time_days"], barmode="group", orientation="h", title=text("coverage"))
            st.plotly_chart(style_chart(fig), width="stretch", key="overview_coverage")
    with st.expander(text("signals")):
        for _, row in queue.head(5).iterrows():
            st.write(f"**{row.sku}**")
            signals(row, audit.loc[audit.sku.eq(row.sku)])


def forecast(forecasts, audit, row, partner=None):
    st.subheader(text("forecast_heading"))
    st.caption(text("forecast_caption"))
    columns = st.columns(4)
    for col, key in zip(columns, ["baseline_demand", "seasonal_factor", "growth_factor", "forecast_demand"]):
        with col:
            metric(text(f"col_{key}"), row.get(key))
    history = audit.loc[audit.sku.eq(row.sku)]
    chart = go.Figure()
    observed = history.get("raw_sales", history.sales)
    chart.add_trace(go.Scatter(x=history.date, y=observed, mode="lines+markers", name=text("historical_sales"), line=dict(color="#849775", width=2)))
    anomalies = history.loc[history.is_anomaly.astype(bool)]
    chart.add_trace(go.Scatter(x=anomalies.date, y=anomalies.get("raw_sales", anomalies.sales), mode="markers", name=text("sales_spike"), marker=dict(color=PALETTE["CRITICAL"], size=9)))
    chart.add_trace(go.Scatter(x=history.date, y=history.adjusted_demand.where(~history.is_anomaly.astype(bool)), mode="lines", name=text("derived"), line=dict(color="#AD7359", dash="dash")))
    stockouts = history.loc[history.is_stockout.astype(bool)]
    chart.add_trace(go.Scatter(x=stockouts.date, y=stockouts.sales, mode="markers", name="Stockout", marker=dict(color=PALETTE["MEDIUM"], size=9, symbol="diamond")))
    for key, label, dash in [("baseline_demand", "recent_baseline", "dash"), ("forecast_demand", "next_forecast", "dot")]:
        if intelligence.number(row.get(key)) is not None and len(history):
            chart.add_trace(go.Scatter(x=[history.date.min(), row.forecast_month], y=[row[key]] * 2, mode="lines", name=text(label), line=dict(dash=dash)))
    chart.update_layout(xaxis_title=text("month"), yaxis_title=text("sales_units_month"))
    st.plotly_chart(style_chart(chart, 370), width="stretch", key="forecast_history")
    signals(row, history)
    with st.expander(text("why")):
        explanation = clean_text(load_engine().forecasting.explain_forecast(row, st.session_state.language))
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-ZА-Я])", explanation):
            st.write(sentence)
    with st.expander(text("full_table")):
        show_table(forecasts[FORECAST_COLUMNS].round(2))
    with st.expander(text("forecast_audit")):
        st.write(text("raw_mean").format(value=row.raw_demand_estimate) if intelligence.number(row.raw_demand_estimate) is not None else text("unavailable"))
        show_table(history)
        if partner is not None:
            show_table(partner.history.loc[partner.history.sku.eq(row.sku)])
            documents = partner.documents.loc[partner.documents.sku.eq(row.sku)].sort_values(["is_document_anomaly", "date"], ascending=[False, False])
            st.caption(f"{len(documents):,} documents; first 100 shown. Document-level detection; customer identifiers were not supplied.")
            show_table(documents.head(100))
            st.download_button("Export selected SKU document audit", documents.to_csv(index=False), file_name=f"{row.sku}_documents.csv", mime="text/csv", key="documents_export")


def detail(row):
    name = translate_product_name(row.product_name, st.session_state.language)
    st.subheader(name)
    st.caption(f"{row.sku} · {row.supplier} · {row.urgency} · {row.status}")
    for keys in [["current_stock", "in_transit", "forecast_demand", "recommended_order_qty"],
                 ["safety_stock", "target_stock", "lead_time_days", "current_coverage_days"]]:
        for col, key in zip(st.columns(4), keys):
            with col:
                metric(text(f"col_{key}"), row.get(key))
    st.subheader(text("why"))
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.write(f"**{text('forecast')}**")
        for key in ["baseline_demand", "anomaly_adjustment", "stockout_adjustment", "seasonal_adjustment", "growth_adjustment", "forecast_demand"]:
            st.write(f"{text('col_' + key)}: {intelligence.format_number(row.get(key), text('unavailable'))}")
    with right, st.container(border=True):
        st.write(f"**{text('replenishment')}**")
        for key in ["lead_time_demand", "safety_stock", "target_stock", "inventory_position", "raw_required_qty", "minimum_order_qty", "order_multiple", "recommended_order_qty"]:
            st.write(f"{text('col_' + key)}: {intelligence.format_number(row.get(key), text('unavailable'))}")
    st.caption(clean_text(row.urgency_reason))
    with st.expander(text("why") + " · " + text("full_table")):
        explanation = clean_text(load_engine().explanations.explain_replenishment(row, st.session_state.language))
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-ZА-Я])", explanation):
            st.write(sentence)
    with st.expander(text("provenance")):
        st.caption(text("source_note"))
        source_fields = [key for key in ["sku", "product_name", "supplier", "category", "warehouse", "inventory_scope", "current_stock", "in_transit", "stock_as_of", "stock_source", "transit_source", "lead_time_days", "lead_time_source", "minimum_order_qty", "order_multiple"] if key in row]
        st.write(f"**{text('source')}**")
        show_table(pd.DataFrame({"Field": source_fields, "Value": [clean_text(row.get(k)) if pd.notna(row.get(k)) else text("unavailable") for k in source_fields]}))
        st.write(f"**{text('derived')}**")
        calculation = row[CALCULATION_COLUMNS].rename_axis(text("calculation")).reset_index(name=text("value"))
        show_table(calculation)


def replenishment(page, selected):
    st.subheader(text("replenishment_heading"))
    st.caption(text("replenishment_caption"))
    show_table(page[DECISION_COLUMNS], urgency=True)
    with st.expander(text("full_table")):
        show_table(page, urgency=True)
    fig = px.bar(page, x="sku", y="recommended_order_qty", color="urgency", color_discrete_map=PALETTE,
                 title=text("quantity_chart"), labels={"recommended_order_qty": text("units_to_order")})
    st.plotly_chart(style_chart(fig), width="stretch", key="recommended_quantities")
    detail(selected)


def warehouse(page, selected_sku, renderer=None):
    """Integration boundary: a teammate can pass its renderer without engine changes.

    Renderer contract: renderer(recommendations=<copy>, selected_sku=<real SKU>).
    Never guesses storage positions or builds fake 3D inventory.
    """
    if renderer is not None:
        renderer(recommendations=page.copy(deep=True), selected_sku=selected_sku)
    else:
        st.caption(text("no_3d"))
    values = page.copy()
    for key in ["current_stock", "in_transit", "target_stock"]:
        values[key] = pd.to_numeric(values[key], errors="coerce").astype(float)
    fig = px.bar(values, x="sku", y=["current_stock", "in_transit", "target_stock"], barmode="group", title=text("inventory_chart"))
    fig.for_each_trace(lambda trace: trace.update(name=text(f"stock_{trace.name}")))
    st.plotly_chart(style_chart(fig, 410), width="stretch", key="warehouse_inventory")


def copilot(recommendations, selected, audit):
    st.subheader("AI Procurement Copilot")
    st.caption(text("copilot_note"))
    language = st.session_state.language
    available = llm_available()
    use_model = st.checkbox("Optional real LLM: select verified evidence sentences", value=False, disabled=not available, key="optional_llm")
    if available:
        st.caption("Explicit opt-in: sends the generated evidence sentences to your configured HTTPS provider. The model can only select/reorder these sentences; the full deterministic brief remains visible.")
    else:
        st.caption("Mode A: deterministic. Mode B unavailable: no configured LLM endpoint, model and key. No model request will be made.")
    fingerprint = intelligence.context_fingerprint(recommendations, language, str(use_model))
    if st.button(text("generate"), type="primary", key="generate_brief"):
        brief = intelligence.executive_brief(recommendations)
        sections = brief_sections(brief, language)
        model_lines, error = llm_brief(sections) if use_model and available else ([], None)
        st.session_state["decision_brief"] = {"context": fingerprint, "sections": sections, "model_lines": model_lines, "error": error}
    cached = st.session_state.get("decision_brief", {})
    if cached.get("context") == fingerprint:
        st.subheader(text("brief"))
        if cached["error"]:
            st.warning(cached["error"])
        if cached["model_lines"]:
            with st.container(border=True):
                st.caption("LLM-selected evidence · verified sentences only")
                for line in cached["model_lines"]:
                    st.write(line)
        for key, lines in cached["sections"].items():
            st.write(f"**{text(key)}**")
            for line in lines:
                st.write(line)
    else:
        st.caption(text("stale"))
    st.divider()
    st.write(f"**{selected.sku} · {translate_product_name(selected.product_name, language)}**")
    sku_context = intelligence.context_fingerprint(pd.DataFrame([selected]), language)
    if st.button(text("analyze"), key="analyze_sku"):
        result = intelligence.sku_analysis(selected, audit.loc[audit.sku.eq(selected.sku)])
        st.session_state["sku_analysis"] = {"context": sku_context, "result": result}
    cached_sku = st.session_state.get("sku_analysis", {})
    if cached_sku.get("context") == sku_context:
        result = cached_sku["result"]
        for heading in ["recommendation", "risk", "quality", "next"]:
            with st.container(border=True):
                st.write(f"**{text(heading)}**")
                if heading == "recommendation":
                    st.write(f"{text('col_recommended_order_qty')}: {intelligence.format_number(result['values']['recommended_order_qty'], text('unavailable'))}")
                    for key in ["forecast_demand", "inventory_position", "current_coverage_days", "position_coverage_days", "lead_time_days", "lead_time_demand", "safety_stock", "target_stock", "raw_required_qty"]:
                        st.write(f"{text('col_' + key)}: {intelligence.format_number(result['values'][key], text('unavailable'))}")
                elif heading == "risk":
                    st.write(f"{result['urgency']}: {clean_text(result['urgency_reason'])}")
                    signals(selected, audit.loc[audit.sku.eq(selected.sku)])
                elif heading == "quality":
                    st.write(text(result["quality"]))
                    for reason in result["quality_reasons"]:
                        st.write(risk_label(reason, language))
                    st.caption(text("quality_rule"))
                else:
                    st.write(text(result["next_action"]))
        with st.expander(text("forecast")):
            st.write(clean_text(load_engine().forecasting.explain_forecast(selected, language)))


def _save_quantity(sku, widget_key):
    qty = st.session_state[widget_key]
    if qty is not None:
        st.session_state["manager_reviews"] = load_engine().manager_review.set_review_quantity(st.session_state["manager_reviews"], sku, qty)


def approvals(recommendations, selected, page_skus):
    engine = load_engine()
    sku = selected.sku
    st.subheader(text("workflow"))
    st.caption(text("session_only"))
    record = st.session_state["manager_reviews"][sku]
    original = record["original_order_qty"]
    left, middle, right = st.columns(3)
    with left:
        metric(text("calculated"), original)
    with middle:
        metric(text("reviewed"), record["manager_order_qty"])
    with right:
        st.write(f"**{text('approval')}**")
        st.write(record["decision_state"])
    if record["calculation_changed"]:
        st.warning("Calculation inputs changed. Manual overrides were retained; review and approve again.")
    if selected.status == "REVIEW":
        st.warning(clean_text(selected.review_reasons.replace(";", "; ").replace("_", " ")))
    key = f"manager_quantity_{sku}"
    st.session_state[key] = record["manager_order_qty"]
    st.number_input("Manager order quantity", min_value=0, step=1, key=key, on_change=_save_quantity, args=(sku, key),
                    help="Separate from the read-only calculated recommendation. Zero is a valid reviewed decision.")
    if record["manager_order_qty"] != original:
        st.info(f"Manager changed the quantity: calculated {intelligence.format_number(original)}, reviewed {intelligence.format_number(record['manager_order_qty'])}.")
    if record["manager_order_qty"] is not None:
        compliant = engine.replenishment.apply_order_constraints(record["manager_order_qty"], selected.get("minimum_order_qty"), selected.get("order_multiple"))
        if compliant != record["manager_order_qty"]:
            st.warning(f"Reviewed quantity does not meet supplier constraints. Compliant quantity: {compliant}. The calculated recommendation remains unchanged.")
    if st.button("Approve reviewed quantity", type="primary", key=f"approve_{sku}", disabled=record["manager_order_qty"] is None or record["decision_state"] == "Approved"):
        st.session_state["manager_reviews"] = engine.manager_review.approve_review(st.session_state["manager_reviews"], sku)
        record = st.session_state["manager_reviews"][sku]
    if record["decision_state"] == "Approved":
        st.success(f"Approved (demo only): {record['approved_quantity']} units at {record['approved_at']}. No supplier order was sent.")
    else:
        st.caption("Decision state: Pending")
    reviewed = engine.manager_review.reviewed_recommendations(recommendations, st.session_state["manager_reviews"])
    show_table(reviewed.loc[reviewed.sku.isin(page_skus), [*DECISION_COLUMNS, "manager_order_qty", "quantity_changed", "decision_state", "approved_at"]], urgency=True)
    st.download_button("Export reviewed recommendations (all filtered SKUs)", reviewed.to_csv(index=False), file_name="reviewed_recommendations.csv", mime="text/csv", key="review_export")
    with st.expander(text("history")):
        if record["history"]:
            show_table(pd.DataFrame(record["history"]))
        else:
            st.caption(text("unavailable"))
    st.subheader(text("supplier_heading"))
    lines, totals = engine.replenishment.supplier_proposals(reviewed, "manager_order_qty")
    st.caption("All filtered SKUs. Supplier totals use manager-reviewed quantities, including Pending proposals. Missing purchase prices leave values unknown. Approval sends nothing.")
    show_table(totals)
    for supplier, group in lines.groupby("supplier", sort=True):
        with st.expander(text("proposed_units").format(supplier=supplier, quantity=int(group.manager_order_qty.sum()))):
            show_table(group.head(50), urgency=True)
            st.caption(f"{len(group)} proposal lines; up to 50 shown. Complete lines are in the export.")
            st.download_button("Export supplier proposal", group.to_csv(index=False), file_name="supplier_proposal.csv", mime="text/csv", key=f"supplier_export_{supplier}")
