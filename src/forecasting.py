"""Explainable next-month demand forecasts for monthly SKU sales histories."""

import pandas as pd
from src.translations import TRANSLATIONS

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


def forecast_demand(data: pd.DataFrame, recent_months: int = 6, seasonal_profiles=None, forecast_month=None):
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
        last_month = (pd.Timestamp(forecast_month).to_period("M") - 1
                      if forecast_month is not None else latest["date"].to_period("M"))
        recent = history.loc[history["date"].dt.to_period("M") > last_month - recent_months]
        regular = recent.loc[~recent["is_anomaly"] & recent["adjusted_demand"].notna()]
        if regular.empty:
            regular = history.loc[~history["is_anomaly"] & history["adjusted_demand"].notna()].tail(recent_months)
        baseline = float(regular["adjusted_demand"].median()) if len(regular) else float("nan")
        sales_baseline = float(regular["sales"].median()) if len(regular) else float("nan")
        supplied = (seasonal_profiles or {}).get(latest.get("brand"))
        profile = pd.Series(supplied) if supplied else _seasonal_profile(history)
        source = "partner_brand" if supplied else ("sku" if profile is not None else "none")
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
            "raw_demand_estimate": float(recent.get("raw_sales", recent["sales"]).mean()),
            "seasonal_factor": factor, "forecast_demand": seasonal_forecast * growth["growth_factor"],
            "anomalies_detected": int(history["is_anomaly"].sum()),
            "baseline_anomalies_excluded": int(recent["is_anomaly"].sum()),
            "baseline_observations": len(regular), "forecast_month": target,
            "seasonal_source": source,
            "raw_sales_baseline": sales_baseline,
            "anomaly_adjustment": sales_baseline - float(recent.get("raw_sales", recent["sales"]).mean()),
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


def explain_forecast(row: pd.Series, language: str = "en") -> str:
    """Describe calculated evidence without changing any forecast values."""
    tr = TRANSLATIONS.get(language, TRANSLATIONS["en"])
    text = tr["ex_f1"].format(
        n=int(row["baseline_observations"]), value=row["baseline_demand"],
        excluded=int(row["baseline_anomalies_excluded"]),
    )
    text += tr.get("ex_stockout", TRANSLATIONS["en"]["ex_stockout"]).format(
        sales=row["raw_sales_baseline"], adjustment=row["stockout_adjustment"],
        periods=int(row["stockout_periods"]), lost=row["estimated_lost_demand"],
    )
    if row["stockout_unresolved"] or row["stockout_fallback_periods"]:
        text += tr.get("ex_stockout_review", TRANSLATIONS["en"]["ex_stockout_review"]).format(
            unresolved=int(row["stockout_unresolved"]), fallback=int(row["stockout_fallback_periods"]),
        )
    if row["stockout_metadata_missing"]:
        if "possible_stockout_periods" not in row:
            text += tr.get("ex_stockout_missing", TRANSLATIONS["en"]["ex_stockout_missing"])
        else:
            text += "Stockout duration is unavailable for some rows. Only explicit zero historical inventory can support a possible-stockout estimate; blank inventory is unknown. "
    if "document_anomalies_detected" in row:
        text += (f"{int(row['document_anomalies_detected'])} isolated document spike(s) were removed from reconciled monthly demand, "
                 f"totalling {row['document_excluded_qty']:.1f} units; original documents remain in the audit. "
                 f"{int(row['document_anomalies_unreconciled'])} other document flags could not be reconciled and require review. "
                 "Customer-level detection is unavailable: no customer identifier was supplied. ")
    if row.get("possible_stockout_periods", 0):
        text += "Possible stockouts use comparable-month demand above observed sales, based on zero inventory snapshots; outage duration is unknown and manager review is required. "
    if row["seasonal_source"] == "none":
        text += tr["ex_no_season"]
    else:
        change = (row["seasonal_factor"] - 1) * 100
        direction = tr["direction_up"] if change >= 0 else tr["direction_down"]
        source = {"sku": tr.get("source_sku", "SKU"), "category": tr.get("source_category", "category")}.get(row["seasonal_source"], row["seasonal_source"])
        text += tr["ex_season"].format(source=source, direction=direction, change=abs(change))
    if row["growth_detected"]:
        text += tr.get("ex_growth", TRANSLATIONS["en"]["ex_growth"]).format(
            n=int(row["growth_observations"]), change=(row["growth_factor"] - 1) * 100,
            adjustment=row["growth_adjustment"],
        )
    else:
        text += tr.get("ex_no_growth", TRANSLATIONS["en"]["ex_no_growth"]).format(
            reason=row["growth_reason"].replace("_", " "),
        )
    month = row["forecast_month"].strftime("%B %Y")
    if language in {"ru", "kz"}:
        month_names = {
            "ru": ["??????", "???????", "?????", "??????", "???", "????", "????", "???????", "????????", "???????", "??????", "???????"],
            "kz": ["??????", "?????", "??????", "?????", "?????", "??????", "?????", "?????", "????????", "?????", "??????", "?????????"],
        }
        month = f"{month_names[language][row['forecast_month'].month - 1]} {row['forecast_month'].year}"
    return text + tr["ex_forecast"].format(month=month, value=row["forecast_demand"])
