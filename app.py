"""SOLAI procurement workspace. Calculations stay in the existing engine modules."""

import importlib
from pathlib import Path

import streamlit as st

from src import engine as engine_loader
from src import dashboard_data as data_layer
from src import dashboard_views as views
from src.decision_intelligence import filter_recommendations
from src.partner_loader import workbook_fingerprint
from src.presentation import apply_theme, text

if (getattr(engine_loader, "EXPECTED_RECOMMENDATION_SCHEMA_VERSION", None) != 4
        or getattr(engine_loader, "EXPECTED_EXPLANATION_INTERFACE_VERSION", None) != 1):
    engine_loader = importlib.reload(engine_loader)
engine = engine_loader.load_engine()

st.set_page_config(page_title="SOLAI | Inventory Intelligence", layout="wide")
st.session_state.setdefault("language", "en")
apply_theme()
header, languages = st.columns([4, 1.3])
with header:
    st.markdown('<div class="solai-brand"><h1>SOLAI</h1><span>Inventory Intelligence</span></div>', unsafe_allow_html=True)
    st.caption(text("subtitle"))
with languages:
    st.radio(text("language"), ["EN", "RU", "KZ"], key="language_choice",
             index=["en", "ru", "kz"].index(st.session_state.language),
             on_change=lambda: setattr(st.session_state, "language", st.session_state.language_choice.lower()),
             horizontal=True, label_visibility="collapsed")

root = Path(__file__).parent
has_partner = bool(workbook_fingerprint(root / "data/raw"))
mode = st.sidebar.selectbox("Data source", ["Partner data", "Synthetic demo"],
                            index=0 if has_partner else 1, key="data_source")
is_partner = mode == "Partner data"
partner = None
try:
    fingerprint = data_layer.source_fingerprint(is_partner)
    if is_partner:
        partner = data_layer.partner_data(fingerprint)
        suppliers = sorted(partner.catalog.supplier.dropna().unique())
        source_note = f"Partner-provided Excel | {len(partner.catalog):,} catalog SKUs | Snapshot {partner.as_of:%Y-%m-%d}"
        supplier_options = suppliers
    else:
        sample = data_layer.load_sample_data(root / "data/sample_data.csv")
        suppliers = sorted(sample.supplier.dropna().unique())
        source_note = f"Synthetic demo | {sample.sku.nunique()} catalog SKUs | sample_data.csv"
        supplier_options = ["", *suppliers]
    all_suppliers_label = text("all")
    if "supplier_filter" in st.session_state and st.session_state["supplier_filter"] not in supplier_options:
        st.session_state["supplier_filter"] = supplier_options[0]
    supplier = st.sidebar.selectbox("Supplier / brand", supplier_options,
        format_func=lambda value, label=all_suppliers_label: value or label, key="supplier_filter")
    search = st.sidebar.text_input(text("search"), key="product_search")
    urgencies = st.sidebar.multiselect(text("urgency_filter"), ["CRITICAL", "HIGH", "MEDIUM", "LOW"], key="urgency_filter")
    statuses = st.sidebar.multiselect(text("status_filter"), ["ORDER", "COVERED", "REVIEW"], key="status_filter")
    with st.sidebar.expander(text("settings"), expanded=is_partner):
        lead_days = st.number_input("Planning lead time (days)", min_value=0, value=None, step=1,
            key="planning_lead_days", help="Absent from partner files. Explicit manager planning assumption, not an imported supplier fact.") if is_partner else None
        service_factor = st.number_input(text("service_factor"), min_value=0.0, value=1.65, step=.05,
                                         key="service_factor", help=text("service_help"))
    forecasts, audit, calculated = data_layer.recommendation_bundle(mode, fingerprint, supplier or None, lead_days, service_factor)
except (OSError, ValueError) as exc:
    st.error(f"Could not load selected data: {exc}")
    st.stop()

st.caption(source_note)
if is_partner:
    with st.sidebar.expander(text("provenance")):
        st.caption("September is partial and excluded from fitting. Inventory is not consistently warehouse-allocated. Missing lead time, stock and transit remain unknown; manager assumptions are labelled.")
# State sync uses canonical calculated rows, never translations or a filtered display copy.
st.session_state["manager_reviews"] = engine.manager_review.sync_review_state(calculated, st.session_state.get("manager_reviews", {}))
filtered = filter_recommendations(calculated, search=search, urgencies=urgencies, statuses=statuses)
if filtered.empty:
    st.info(text("empty"))
    st.stop()
filtered = filtered.sort_values("sku").reset_index(drop=True)
page_count = (len(filtered) + 24) // 25
if st.session_state.get("results_page", 1) > page_count:
    st.session_state["results_page"] = 1
page = st.sidebar.selectbox("Results page (25 SKUs)", range(1, page_count + 1), key="results_page")
visible = filtered.iloc[(page - 1) * 25:page * 25]
page_skus = visible.sku.tolist()
if st.session_state.get("selected_sku") not in page_skus:
    known = visible.loc[visible.forecast_demand.notna(), "sku"]
    st.session_state["selected_sku"] = known.iloc[0] if len(known) else page_skus[0]
st.caption(text("scope").format(total=len(filtered), visible=len(visible), page=page, pages=page_count))
selected_sku = st.selectbox(text("sku"), page_skus, key="selected_sku")
selected = visible.loc[visible.sku.eq(selected_sku)].iloc[0]
# Shared selection is also the integration boundary for any teammate warehouse renderer.
st.session_state["warehouse_selected_sku"] = selected_sku

overview_tab, forecast_tab, replenishment_tab, warehouse_tab, copilot_tab, approvals_tab = st.tabs(
    [text(key) for key in ["overview", "forecast", "replenishment", "warehouse", "copilot", "approvals"]])
with overview_tab:
    views.overview(filtered, audit)
with forecast_tab:
    views.forecast(forecasts.loc[forecasts.sku.isin(page_skus)], audit, selected, partner)
with replenishment_tab:
    views.replenishment(visible, selected)
with warehouse_tab:
    # Existing or future 3D work belongs behind this renderer boundary; do not invent coordinates.
    views.warehouse(visible, selected_sku)
with copilot_tab:
    views.copilot(filtered, selected, audit)
with approvals_tab:
    views.approvals(filtered, selected, page_skus)
