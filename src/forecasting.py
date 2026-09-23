"""Explainable next-month demand forecasts for monthly SKU sales histories."""

import pandas as pd

from src.anomaly_detection import detect_sales_anomalies


SUMMARY_COLUMNS = [
    "sku", "product_name", "category", "supplier", "baseline_demand",
    "seasonal_factor", "forecast_demand", "anomalies_detected",
    "current_stock", "in_transit", "lead_time_days", "raw_demand_estimate",
    "forecast_month", "seasonal_source", "baseline_observations",
    "baseline_anomalies_excluded",
]


def _seasonal_profile(history: pd.DataFrame):
    """Calendar-month indices from at least two complete non-anomalous years."""
    clean = history.loc[~history["is_anomaly"]].copy()
    clean["year"] = clean["date"].dt.year
    clean["month"] = clean["date"].dt.month
    years = []
    for _, year in clean.groupby("year"):
        if year["month"].nunique() == 12 and year["sales"].median() > 0:
            years.append(year.set_index("month")["sales"] / year["sales"].median())
    return pd.concat(years, axis=1).median(axis=1) if len(years) >= 2 else None


def forecast_demand(data: pd.DataFrame, recent_months: int = 6):
    """Return (SKU summary, full history with anomaly audit flags).

    Raw demand is the recent mean including spikes; baseline is the recent
    non-spike median. Forecast = baseline * seasonal factor. The factor is
    target-month index / median index of baseline months, adjusting relative
    to the recent season. Profiles need two complete clean calendar years.
    Eligible category peers supply normalized median profiles as fallback.
    Missing months are unknown, not zero. Inventory is the latest snapshot.
    """
    if not isinstance(recent_months, int) or recent_months < 1:
        raise ValueError("recent_months must be a positive integer")
    audit = detect_sales_anomalies(data)
    rows = []
    for sku, history in audit.groupby("sku", sort=True):
        latest = history.iloc[-1]
        last_month = latest["date"].to_period("M")
        recent = history.loc[history["date"].dt.to_period("M") > last_month - recent_months]
        regular = recent.loc[~recent["is_anomaly"]]
        if regular.empty:
            regular = history.loc[~history["is_anomaly"]].tail(recent_months)
        baseline = float(regular["sales"].median()) if len(regular) else 0.0
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
        rows.append({
            **{key: latest[key] for key in ["product_name", "category", "supplier",
                                          "current_stock", "in_transit", "lead_time_days"]},
            "sku": sku, "baseline_demand": baseline,
            "raw_demand_estimate": float(recent["sales"].mean()),
            "seasonal_factor": factor, "forecast_demand": baseline * factor,
            "anomalies_detected": int(history["is_anomaly"].sum()),
            "baseline_anomalies_excluded": int(recent["is_anomaly"].sum()),
            "baseline_observations": len(regular), "forecast_month": target,
            "seasonal_source": source,
        })
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS), audit


def explain_forecast(row: pd.Series) -> str:
    """Describe the calculated evidence for a selected SKU."""
    text = (
        f"Regular demand is the median of {int(row['baseline_observations'])} "
        f"non-anomalous monthly observations: {row['baseline_demand']:.1f} units. "
        f"{int(row['baseline_anomalies_excluded'])} unusual sales spike(s) were excluded "
        "from the recent baseline calculation and retained in the audit history. "
    )
    if row["seasonal_source"] == "none":
        text += "There is insufficient reliable seasonal history; the seasonal factor is 1.00. "
    else:
        change = (row["seasonal_factor"] - 1) * 100
        direction = "increases" if change >= 0 else "decreases"
        text += (f"Seasonality from {row['seasonal_source']} history {direction} "
                 f"the forecast by {abs(change):.1f}% relative to recent months. ")
    return text + (f"Forecast for {row['forecast_month']:%B %Y}: "
                   f"{row['forecast_demand']:.1f} units. The final decision remains with the manager.")
