"""Pure calculations for manager-reviewed proposals; no order sending."""

from math import ceil, isfinite, sqrt

import pandas as pd


CALCULATED_COLUMNS = [
    "daily_demand", "lead_time_demand", "monthly_demand_std", "safety_stock",
    "target_stock", "inventory_position", "raw_order_qty", "recommended_order_qty",
    "unit_price", "estimated_order_value", "service_level_factor",
    "history_observations", "safety_stock_method", "anomalies_excluded",
    "review_reasons", "status",
]


def _nonnegative_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) and number >= 0 else None


def calculate_replenishment(
    forecasts: pd.DataFrame,
    audit_history: pd.DataFrame,
    service_level_factor: float = 1.65,
    variability_months: int = 12,
) -> pd.DataFrame:
    """Use Phase 1 forecasts and its retained, flagged monthly history.

    Daily demand = monthly forecast / 30. With at least three clean monthly
    observations, use sample standard deviation (ddof=1) of recent sales.
    Safety stock = service factor * monthly std * sqrt(lead time / 30).
    This approximates independent daily demand from monthly totals; it is
    not a calibrated service guarantee. With less history, monthly std is
    max(forecast, clean monthly mean), a conservative 100% variability proxy.
    Missing months are unknown. REVIEW overrides ORDER/COVERED for anomalies,
    gaps, insufficient history, invalid values, or missing supplier/price.
    Invalid quantity inputs yield no quantity (NA), never false coverage.
    """
    factor = _nonnegative_number(service_level_factor)
    if factor is None:
        raise ValueError("service_level_factor must be finite and nonnegative")
    if not isinstance(variability_months, int) or variability_months < 3:
        raise ValueError("variability_months must be an integer of at least 3")
    if forecasts["sku"].isna().any() or forecasts["sku"].duplicated().any():
        raise ValueError("Forecasts must contain one row per non-missing SKU")
    audit = audit_history.copy()
    audit["date"] = pd.to_datetime(audit["date"], errors="raise")
    if audit["is_anomaly"].isna().any() or not audit["is_anomaly"].isin([True, False]).all():
        raise ValueError("Audit history must contain boolean Phase 1 anomaly flags")
    rows = []
    for _, forecast in forecasts.iterrows():
        row = forecast.to_dict()
        reasons = []
        values = {}
        for key in ["forecast_demand", "lead_time_days", "current_stock", "in_transit"]:
            values[key] = _nonnegative_number(row.get(key))
            if values[key] is None:
                reasons.append(f"invalid_{key}")
        if pd.isna(row.get("supplier")) or not str(row.get("supplier", "")).strip():
            reasons.append("missing_supplier")
        history = audit.loc[
            (audit["sku"] == row["sku"])
            & (audit["date"] < pd.Timestamp(row["forecast_month"]))
        ].sort_values("date")
        cutoff = pd.Timestamp(row["forecast_month"]).to_period("M")
        recent = history.loc[history["date"].dt.to_period("M") >= cutoff - variability_months]
        clean = recent.loc[~recent["is_anomaly"].astype(bool)]
        observations = pd.to_numeric(clean["sales"], errors="coerce")
        if any(_nonnegative_number(v) is None for v in observations):
            reasons.append("invalid_history_sales")
            observations = observations.loc[observations.map(_nonnegative_number).notna()]
        anomalies = int(history["is_anomaly"].sum())
        if anomalies:
            reasons.append("sales_anomalies_detected")
        periods = recent["date"].dt.to_period("M")
        if periods.duplicated().any():
            raise ValueError("Audit history must have one observation per SKU/month")
        if len(periods) and (cutoff.ordinal - periods.min().ordinal != len(periods)):
            reasons.append("missing_history_months")
        fallback = len(observations) < 3
        if fallback:
            reasons.append("insufficient_history")
            sigma = max(values["forecast_demand"] or 0.0,
                        float(observations.mean()) if len(observations) else 0.0)
        else:
            sigma = float(observations.std(ddof=1))
        price = _nonnegative_number(history.iloc[-1].get("unit_price")) if len(history) else None
        if price is None:
            reasons.append("invalid_unit_price")
        row.update({
            "service_level_factor": factor, "monthly_demand_std": sigma,
            "history_observations": len(observations),
            "safety_stock_method": "fallback_100pct_monthly" if fallback else "historical_sample_std",
            "anomalies_excluded": int(recent["is_anomaly"].sum()),
            "anomalies_detected": anomalies, "unit_price": price,
        })
        for key in ["daily_demand", "lead_time_demand", "safety_stock", "target_stock",
                    "inventory_position", "raw_order_qty", "recommended_order_qty",
                    "estimated_order_value"]:
            row[key] = None
        if all(v is not None for v in values.values()):
            row["daily_demand"] = values["forecast_demand"] / 30
            row["lead_time_demand"] = row["daily_demand"] * values["lead_time_days"]
            row["safety_stock"] = factor * sigma * sqrt(values["lead_time_days"] / 30)
            row["target_stock"] = row["lead_time_demand"] + row["safety_stock"]
            row["inventory_position"] = values["current_stock"] + values["in_transit"]
            row["raw_order_qty"] = row["target_stock"] - row["inventory_position"]
            if all(isfinite(row[k]) for k in ["target_stock", "inventory_position", "raw_order_qty"]):
                row["recommended_order_qty"] = max(0, ceil(row["raw_order_qty"]))
                if price is not None:
                    row["estimated_order_value"] = row["recommended_order_qty"] * price
            else:
                reasons.append("nonfinite_calculation")
        row["review_reasons"] = ";".join(reasons)
        row["status"] = "REVIEW" if reasons else (
            "ORDER" if row["recommended_order_qty"] > 0 else "COVERED"
        )
        rows.append(row)
    result = pd.DataFrame(rows, columns=list(dict.fromkeys([*forecasts.columns, *CALCULATED_COLUMNS])))
    result["recommended_order_qty"] = result["recommended_order_qty"].astype("Int64")
    return result


def supplier_proposals(recommendations: pd.DataFrame):
    """Return positive-quantity proposal lines and supplier totals, including REVIEW.

    Missing prices make the supplier's total value unknown, not understated.
    Zero-quantity and uncalculable recommendations stay in the main review table.
    """
    columns = ["supplier", "sku", "product_name", "recommended_order_qty",
               "unit_price", "estimated_order_value", "status", "review_reasons"]
    lines = recommendations.loc[recommendations["recommended_order_qty"].gt(0).fillna(False), columns].copy()
    lines["supplier"] = lines["supplier"].fillna("Unknown supplier").replace(r"^\s*$", "Unknown supplier", regex=True)
    totals = []
    for supplier, group in lines.groupby("supplier", sort=True):
        totals.append({
            "supplier": supplier, "skus_to_order": len(group),
            "total_units": int(group["recommended_order_qty"].sum()),
            "estimated_order_value": group["estimated_order_value"].sum(min_count=len(group)),
            "skus_requiring_review": int(group["status"].eq("REVIEW").sum()),
        })
    return lines, pd.DataFrame(totals, columns=[
        "supplier", "skus_to_order", "total_units", "estimated_order_value", "skus_requiring_review",
    ])
