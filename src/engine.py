"""Load a consistent engine when Streamlit reruns after a schema upgrade."""

import importlib
from types import SimpleNamespace


EXPECTED_FORECAST_SCHEMA_VERSION = 2
EXPECTED_RECOMMENDATION_SCHEMA_VERSION = 4
ADJUSTMENT_COLUMNS = {
    "raw_sales_baseline", "stockout_adjustment", "estimated_lost_demand", "growth_factor",
}


def load_engine():
    """Refresh legacy imported modules once, in dependency order.

    Streamlit reruns app.py inside an existing Python process. Imported modules
    can still implement the previous forecast schema during an app update.
    Reload the actual calculations, not the output dataframe: no adjustment is
    fabricated and no missing column is silently discarded or filled.
    """
    names = ("demand_adjustments", "anomaly_detection", "forecasting",
             "replenishment", "explanations", "manager_review")
    modules = {name: importlib.import_module(f"src.{name}") for name in names}
    forecasting = modules["forecasting"]
    if (getattr(forecasting, "FORECAST_SCHEMA_VERSION", None) != EXPECTED_FORECAST_SCHEMA_VERSION
            or not ADJUSTMENT_COLUMNS.issubset(getattr(forecasting, "SUMMARY_COLUMNS", []))
            or getattr(modules["replenishment"], "RECOMMENDATION_SCHEMA_VERSION", None)
            != EXPECTED_RECOMMENDATION_SCHEMA_VERSION):
        importlib.invalidate_caches()
        for name in names:
            modules[name] = importlib.reload(modules[name])
    return SimpleNamespace(**modules)
