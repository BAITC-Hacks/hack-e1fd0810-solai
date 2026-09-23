"""Auditable stockout compensation and conservative sustained-growth detection."""

import numpy as np
import pandas as pd


def prepare_stockout_history(data: pd.DataFrame) -> pd.DataFrame:
    """Optional stockout_days describes unavailable days in each calendar month.

    An absent/blank value is unknown, not evidence of availability. Legacy data
    remains usable, but no stockout is inferred from sales or current inventory.
    """
    result = data.copy()
    result["period_days"] = result["date"].dt.days_in_month
    days = pd.to_numeric(result.get("stockout_days", pd.Series(np.nan, index=result.index)), errors="raise")
    known = days.notna()
    if ((~np.isfinite(days[known])).any() or (days[known] < 0).any()
            or (days[known] > result.loc[known, "period_days"]).any()
            or (days[known] % 1 != 0).any()):
        raise ValueError("stockout_days must be whole days between zero and calendar-month length")
    result["stockout_data_available"] = known
    result["stockout_days"] = days.fillna(0).astype(int)
    result["is_stockout"] = result["stockout_days"] > 0
    if ((result["stockout_days"] == result["period_days"]) & (result["sales"] > 0)).any():
        raise ValueError("A full-month stockout cannot have positive sales")
    return result


def compensate_stockouts(audit: pd.DataFrame) -> pd.DataFrame:
    """Estimate lost units from comparable non-stockout, non-spike daily rates.

    Prefer at least two observations of the same calendar month; otherwise use
    the six nearest months within 12 months of the stockout. The history is
    retrospective, bounded by the forecast origin. Imputed rows never serve
    as comparators. With no peers, >=7 available days permit a flagged own-rate
    fallback. Otherwise demand is unknown (NaN), never assumed to be zero.
    """
    result = audit.copy()
    result["adjusted_demand"] = result["sales"].astype(float)
    result["estimated_lost_demand"] = 0.0
    result["stockout_daily_rate"] = np.nan
    result["stockout_comparators"] = 0
    result["stockout_method"] = "not_needed"
    for _, group in result.groupby("sku", sort=False):
        peers = group.loc[~group["is_stockout"] & ~group["is_anomaly"]]
        for index, period in group.loc[group["is_stockout"]].iterrows():
            same_month = peers.loc[peers["date"].dt.month == period["date"].month]
            if len(same_month) >= 2:
                comparable = same_month
                method = "same_calendar_month"
            else:
                distance = (peers["date"].dt.to_period("M").astype("int64")
                            - period["date"].to_period("M").ordinal).abs()
                comparable = peers.loc[distance.loc[distance <= 12].sort_values(kind="stable").head(6).index]
                method = "nearby_months" if len(comparable) >= 3 else "limited_comparators"
            if len(comparable):
                rate = float((comparable["sales"] / comparable["period_days"]).median())
            else:
                available = period["period_days"] - period["stockout_days"]
                if available >= 7 and not period["is_anomaly"]:
                    rate = float(period["sales"] / available)
                    method = "own_available_days_fallback"
                else:
                    rate = np.nan
                    method = "unresolved"
            lost = rate * period["stockout_days"]
            result.loc[index, ["stockout_daily_rate", "stockout_comparators",
                               "stockout_method", "estimated_lost_demand", "adjusted_demand"]] = [
                rate, len(comparable), method, lost, period["sales"] + lost,
            ]
    return result


def detect_sustainable_growth(history, profile, target, reference_forecast):
    """Six genuine clean observations within nine months must support growth.

    Exclude stockouts and spikes; remove known seasonality. Require >=80% of
    successive changes to be positive and >=10% gain in the last-three median
    over the first-three median. Project a Theil-Sen median pairwise slope to
    next month. Limit the resulting uplift over the seasonal baseline to 50%.
    """
    evidence = {
        "growth_factor": 1.0, "growth_detected": False, "growth_slope": 0.0,
        "growth_observations": 0, "growth_reason": "insufficient_clean_history",
    }
    target_month = target.to_period("M")
    clean = history.loc[
        ~history["is_anomaly"] & ~history["is_stockout"]
        & (history["date"].dt.to_period("M") >= target_month - 9)
    ].tail(6)
    evidence["growth_observations"] = len(clean)
    if len(clean) < 6 or clean.iloc[-1]["date"].to_period("M") < target_month - 2:
        return evidence
    x = clean["date"].dt.to_period("M").astype("int64").to_numpy()
    y = clean["sales"].to_numpy(dtype=float)
    target_index = 1.0
    if profile is not None:
        indices = clean["date"].dt.month.map(profile).to_numpy()
        if (indices <= 0).any():
            evidence["growth_reason"] = "unusable_seasonal_profile"
            return evidence
        y = y / indices
        target_index = float(profile.loc[target.month])
    first, last = float(np.median(y[:3])), float(np.median(y[-3:]))
    persistent = np.count_nonzero(np.diff(y) > max(first * 0.001, 1e-9)) >= 4
    if first <= 0 or last < first * 1.10 or not persistent:
        evidence["growth_reason"] = "no_persistent_growth"
        return evidence
    slope = float(np.median([(y[j] - y[i]) / (x[j] - x[i])
                             for i in range(len(x)) for j in range(i + 1, len(x))]))
    # Center times to avoid large calendar ordinal arithmetic in the intercept.
    x = x - target_month.ordinal
    projection = float(np.median(y - slope * x)) * target_index
    if slope > 0 and reference_forecast > 0 and projection > reference_forecast:
        evidence.update({
            "growth_factor": min(1.5, projection / reference_forecast),
            "growth_detected": True, "growth_slope": slope,
            "growth_reason": "persistent_growth",
        })
    else:
        evidence["growth_reason"] = "no_additional_uplift"
    return evidence
