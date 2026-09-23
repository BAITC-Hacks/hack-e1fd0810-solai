"""Explainable next-month demand forecasts for monthly SKU sales histories."""

import pandas as pd

from src.anomaly_detection import detect_sales_anomalies
from src.demand_adjustments import compensate_stockouts, detect_sustainable_growth


# Increment when the UI/pipeline requires a new forecast-result contract.
FORECAST_SCHEMA_VERSION = 2

SUMMARY_COLUMNS = [
    "sku", "product_name", "category", "supplier", "baseline_demand",
    "seasonal_factor", "forecast_demand", "anomalies_detected",
    "current_stock", "in_transit", "lead_time_days", "raw_demand_estimate",
    "forecast_month", "seasonal_source", "baseline_observations",
    "baseline_anomalies_excluded",
    "raw_sales_baseline", "anomaly_adjustment", "stockout_adjustment",
    "stockout_periods", "estimated_lost_demand", "stockout_unresolved",
    "stockout_fallback_periods", "stockout_metadata_missing",
    "seasonal_adjustment", "growth_factor", "growth_adjustment", "growth_detected",
    "growth_slope", "growth_observations", "growth_reason",
]


def _seasonal_profile(history: pd.DataFrame):
    """Month indices from two complete years of non-spike, estimable demand."""
    clean = history.loc[~history["is_anomaly"] & history["adjusted_demand"].notna()].copy()
    clean["year"] = clean["date"].dt.year
    clean["month"] = clean["date"].dt.month
    years = []
    for _, year in clean.groupby("year"):
        if year["month"].nunique() == 12 and year["adjusted_demand"].median() > 0:
            years.append(year.set_index("month")["adjusted_demand"] / year["adjusted_demand"].median())
    return pd.concat(years, axis=1).median(axis=1) if len(years) >= 2 else None


def forecast_demand(data: pd.DataFrame, recent_months: int = 6):
    """Return (SKU summary, full history with anomaly audit flags).

    Raw demand is the recent mean including spikes; baseline is the recent
    non-spike median after stockout compensation. Forecast = baseline *
    seasonal factor * growth factor. The seasonal factor is
    target-month index / median index of baseline months, adjusting relative
    to the recent season. Profiles need two complete years of estimable demand.
    Eligible category peers supply normalized median profiles as fallback.
    Missing months are unknown, not zero. Inventory is the latest snapshot.
    """
    if not isinstance(recent_months, int) or recent_months < 1:
        raise ValueError("recent_months must be a positive integer")
    audit = compensate_stockouts(detect_sales_anomalies(data))
    rows = []
    for sku, history in audit.groupby("sku", sort=True):
        latest = history.iloc[-1]
        last_month = latest["date"].to_period("M")
        recent = history.loc[history["date"].dt.to_period("M") > last_month - recent_months]
        regular = recent.loc[~recent["is_anomaly"] & recent["adjusted_demand"].notna()]
        if regular.empty:
            regular = history.loc[~history["is_anomaly"] & history["adjusted_demand"].notna()].tail(recent_months)
        baseline = float(regular["adjusted_demand"].median()) if len(regular) else float("nan")
        sales_baseline = float(regular["sales"].median()) if len(regular) else float("nan")
        profile = _seasonal_profile(history)
        source = "sku" if profile is not None else "none"
        if profile is None:
            peers = audit.loc[
                (audit["category"] == latest["category"])
                & (audit["sku"] != sku)
                & (audit["date"].dt.to_period("M") <= last_month)
            ]
            profiles = [p for _, peer in peers.groupby("sku")
                        if (p := _seasonal_profile(peer)) is not None]
            if profiles:
                profile = pd.concat(profiles, axis=1).median(axis=1)
                source = "category"
        factor = 1.0
        target = (last_month + 1).to_timestamp()
        if profile is not None and len(regular):
            recent_index = regular["date"].dt.month.map(profile).median()
            if recent_index > 0:
                factor = float(profile.loc[target.month] / recent_index)
            else:
                source = "none"
        seasonal_forecast = baseline * factor
        growth = detect_sustainable_growth(history, profile, target, seasonal_forecast)
        rows.append({
            **{key: latest[key] for key in ["product_name", "category", "supplier",
                                          "current_stock", "in_transit", "lead_time_days"]},
            "sku": sku, "baseline_demand": baseline,
            "raw_demand_estimate": float(recent["sales"].mean()),
            "seasonal_factor": factor, "forecast_demand": seasonal_forecast * growth["growth_factor"],
            "anomalies_detected": int(history["is_anomaly"].sum()),
            "baseline_anomalies_excluded": int(recent["is_anomaly"].sum()),
            "baseline_observations": len(regular), "forecast_month": target,
            "seasonal_source": source,
            "raw_sales_baseline": sales_baseline,
            "anomaly_adjustment": sales_baseline - float(recent["sales"].mean()),
            "stockout_adjustment": baseline - sales_baseline,
            "stockout_periods": int(history["is_stockout"].sum()),
            "estimated_lost_demand": float(history["estimated_lost_demand"].sum(min_count=1)),
            "stockout_unresolved": int(history["stockout_method"].eq("unresolved").sum()),
            "stockout_fallback_periods": int(history["stockout_method"].isin([
                "limited_comparators", "own_available_days_fallback",
            ]).sum()),
            "stockout_metadata_missing": int((~history["stockout_data_available"]).sum()),
            "seasonal_adjustment": seasonal_forecast - baseline,
            "growth_adjustment": seasonal_forecast * (growth["growth_factor"] - 1),
            **growth,
        })
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS), audit


def explain_forecast(row: pd.Series) -> str:
    """Describe the calculated evidence for a selected SKU."""
    text = (
        f"Regular demand is the median of {int(row['baseline_observations'])} "
        f"non-anomalous monthly demand estimates after stockout compensation: {row['baseline_demand']:.1f} units. "
        f"{int(row['baseline_anomalies_excluded'])} unusual sales spike(s) were excluded "
        "from the recent baseline calculation and retained in the audit history. "
    )
    text += (
        f"Raw-sales baseline after robust spike handling: {row['raw_sales_baseline']:.1f} units. "
        f"Stockout compensation adds {row['stockout_adjustment']:.1f} units to this baseline. "
        f"{int(row['stockout_periods'])} stockout period(s) imply {row['estimated_lost_demand']:.1f} "
        "estimated lost units across the history; these are estimates, not observed sales. "
    )
    if row["stockout_unresolved"] or row["stockout_fallback_periods"]:
        text += (f"Stockout estimates need review: {int(row['stockout_unresolved'])} unresolved period(s), "
                 f"{int(row['stockout_fallback_periods'])} period(s) using limited-history fallback. ")
    if row["stockout_metadata_missing"]:
        text += "Some stockout metadata is unavailable; no stockout was inferred for those rows. "
    if row["seasonal_source"] == "none":
        text += "There is insufficient reliable seasonal history; the seasonal factor is 1.00. "
    else:
        change = (row["seasonal_factor"] - 1) * 100
        direction = "increases" if change >= 0 else "decreases"
        text += (f"Seasonality from {row['seasonal_source']} history {direction} "
                 f"the forecast by {abs(change):.1f}% relative to recent months. ")
    if row["growth_detected"]:
        text += (f"Sustainable growth is supported by {int(row['growth_observations'])} clean months. "
                 f"Growth increases the seasonal forecast by {(row['growth_factor'] - 1) * 100:.1f}% "
                 f"({row['growth_adjustment']:.1f} units; uplift capped at 50%). ")
    else:
        text += f"No growth uplift applied: {row['growth_reason'].replace('_', ' ')}. "
    return text + (f"Forecast for {row['forecast_month']:%B %Y}: "
                   f"{row['forecast_demand']:.1f} units. The final decision remains with the manager.")
