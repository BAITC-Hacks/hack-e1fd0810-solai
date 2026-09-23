"""Cached orchestration of existing engines; the UI does not own business formulas."""

import importlib
from pathlib import Path

import streamlit as st

from src.data_loader import load_sample_data
from src.engine import load_engine
from src.partner_loader import workbook_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def source_fingerprint(partner=True):
    engine_files = ["data_loader.py", "partner_loader.py", "partner_pipeline.py", "engine.py",
                    "forecasting.py", "anomaly_detection.py", "demand_adjustments.py",
                    "replenishment.py", "explanations.py"]
    code = tuple((name, (ROOT / "src" / name).stat().st_mtime_ns) for name in engine_files)
    if partner:
        return (*workbook_fingerprint(ROOT / "data/raw"), *code)
    source = ROOT / "data/sample_data.csv"
    return ((source.name, source.stat().st_size, source.stat().st_mtime_ns), *code)


def partner_data(fingerprint):
    # Presentation changes must not trigger Excel I/O. Only files and adapter code do.
    workbook_key = tuple(item for item in fingerprint if item[0].endswith(".xlsx") or item[0] == "partner_loader.py")
    return _partner_data(workbook_key)


@st.cache_data(show_spinner="Reading partner workbooks...", max_entries=2)
def _partner_data(workbook_key):
    return importlib.import_module("src.partner_loader").load_partner_data(ROOT / "data/raw")


@st.cache_data(show_spinner="Calculating demand for the selected supplier...", max_entries=6)
def forecast_bundle(mode, fingerprint, supplier=None):
    engine = load_engine()
    if mode == "Partner data":
        partner = partner_data(fingerprint)
        skus = partner.catalog.loc[partner.catalog.supplier.eq(supplier)].sku if supplier else None
        forecasts, audit = importlib.import_module("src.partner_pipeline").partner_forecasts(partner, skus)
        return forecasts, audit
    data = load_sample_data(ROOT / "data/sample_data.csv")
    if supplier:
        data = data.loc[data.supplier.eq(supplier)]
    return engine.forecasting.forecast_demand(data)


@st.cache_data(show_spinner="Calculating replenishment recommendations...", max_entries=8)
def recommendation_bundle(mode, fingerprint, supplier, planning_lead_days, service_factor):
    engine = load_engine()
    forecasts, audit = forecast_bundle(mode, fingerprint, supplier)
    if mode == "Partner data" and planning_lead_days is not None:
        # The scenario changes only metadata; all demand and order formulas remain in the engines.
        forecasts, audit = forecasts.copy(), audit.copy()
        for frame in (forecasts, audit):
            missing = frame.lead_time_days.isna()
            frame.loc[missing, "lead_time_days"] = float(planning_lead_days)
            frame.loc[missing, "lead_time_source"] = "manager_planning_assumption"
    recommendations = engine.replenishment.calculate_replenishment(forecasts, audit, service_factor)
    recommendations["explanation"] = recommendations.apply(engine.explanations.explain_replenishment, axis=1)
    return forecasts, audit, recommendations
