"""Human-readable explanations generated only from calculated SKU values."""

import pandas as pd
from src.translations import TRANSLATIONS

from src.forecasting import explain_forecast


def explain_replenishment(row: pd.Series, language: str = "en") -> str:
    """Explain formulas, fallback, adjustments and review conditions."""
    tr = TRANSLATIONS.get(language, TRANSLATIONS["en"])
    parts = [tr["ex_r1"].format(value=row["forecast_demand"])]
    if "urgency" in row:
        parts.append(f"Urgency: {row['urgency']}. {row['urgency_reason']}")
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
            tr["ex_inventory"].replace(
                "(raw need rounded up, with a minimum of zero)", "(after upward rounding and any supplied order constraints)"
            ).replace(
                "(исходная потребность округлена вверх, минимум — ноль)", "(с округлением вверх и учётом ограничений поставщика)"
            ).replace(
                "(бастапқы қажеттілік жоғары қарай дөңгелектеледі, ең азы — нөл)", "(жоғары дөңгелектеу және жеткізуші шектеулері ескерілген)"
            ).format(current=row["current_stock"], transit=row["in_transit"], position=row["inventory_position"], lead=row["lead_time_demand"], safety=row["safety_stock"], target=row["target_stock"], raw=row["raw_order_qty"], qty=int(row["recommended_order_qty"]))
        )
        if row["recommended_order_qty"] == 0:
            parts.append(tr["ex_zero"])
    else:
        parts.append(tr["ex_unavailable"])
    if "moq_status" in row:
        parts.append(f"Raw required quantity before order constraints: {row.get('raw_required_qty')}. "
                     f"Minimum shipment: {row.get('minimum_order_qty')}; order multiple: {row.get('order_multiple')}. "
                     f"MOQ availability: {row['moq_status']}. Missing or zero constraints do not change the quantity.")
    if row.get("lead_time_source") == "manager_planning_assumption":
        parts.append("Источник срока поставки: Допущение пользователя." if language == "ru"
                     else "Lead-time source: User assumption.")
    elif row.get("lead_time_source"):
        parts.append(f"Lead-time source: {row['lead_time_source'].replace('_', ' ')}.")
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
