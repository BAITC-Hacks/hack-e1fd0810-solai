"""Human-readable explanations generated only from calculated SKU values."""

import pandas as pd
from src.translations import TRANSLATIONS

from src.forecasting import explain_forecast


def explain_replenishment(row: pd.Series, language: str = "en") -> str:
    """Explain formulas, fallback, adjustments and review conditions."""
    tr = TRANSLATIONS.get(language, TRANSLATIONS["en"])
    parts = [tr["ex_r1"].format(value=row["forecast_demand"])]
    if "growth_factor" in row:
        parts.append(explain_forecast(row, language))
    if pd.notna(row["recommended_order_qty"]):
        parts.append(
            tr["ex_daily"].format(daily=row["daily_demand"], days=row["lead_time_days"], lead=row["lead_time_demand"])
        )
        parts.append(
            tr["ex_safety"].format(factor=row["service_level_factor"], std=row["monthly_demand_std"], days=row["lead_time_days"], stock=row["safety_stock"], n=row["history_observations"])
        )
        parts.append(
            tr["ex_inventory"].format(current=row["current_stock"], transit=row["in_transit"], position=row["inventory_position"], lead=row["lead_time_demand"], safety=row["safety_stock"], target=row["target_stock"], raw=row["raw_order_qty"], qty=int(row["recommended_order_qty"]))
        )
        if row["recommended_order_qty"] == 0:
            parts.append(tr["ex_zero"])
    else:
        parts.append(tr["ex_unavailable"])
    if row["safety_stock_method"] == "fallback_100pct_monthly":
        parts.append(tr["ex_fallback"])
    parts.append(tr["ex_anomaly"].format(detected=int(row["anomalies_detected"]), excluded=int(row["anomalies_excluded"])))
    if row["seasonal_factor"] != 1:
        parts.append(tr["ex_seasonal_applied"].format(factor=row["seasonal_factor"]))
    if row.get("stockout_observations_excluded", 0):
        parts.append(tr.get("ex_stockout_excluded", TRANSLATIONS["en"]["ex_stockout_excluded"]).format(n=int(row["stockout_observations_excluded"])))
    if row["status"] == "REVIEW":
        reasons = row["review_reasons"].replace(";", ", ").replace("_", " ")
        parts.append(tr["ex_review"].format(reasons=reasons))
    parts.append(tr["ex_proposal"])
    return " ".join(parts)
