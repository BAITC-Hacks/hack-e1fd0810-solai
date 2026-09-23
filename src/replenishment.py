"""Pure calculations for manager-reviewed proposals; no order sending."""

from math import ceil, isfinite, sqrt

import pandas as pd


RECOMMENDATION_SCHEMA_VERSION = 4
URGENCY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

CALCULATED_COLUMNS = [
    "daily_demand", "lead_time_demand", "monthly_demand_std", "safety_stock",
    "target_stock", "inventory_position", "raw_order_qty", "recommended_order_qty",
    "unit_price", "estimated_order_value", "service_level_factor",
    "history_observations", "safety_stock_method", "anomalies_excluded",
    "review_reasons", "status",
    "stockout_observations_excluded",
    "current_coverage_days", "position_coverage_days", "urgency", "urgency_reason",
    "raw_required_qty", "minimum_order_qty", "order_multiple", "moq_status",
]


def _nonnegative_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) and number >= 0 else None


def apply_order_constraints(requirement, minimum_order_qty=None, order_multiple=None):
    """Round upward only. A minimum shipment is not automatically a multiple."""
    needed = _nonnegative_number(requirement)
    if needed is None:
        return None
    if needed == 0:
        return 0
    minimum = _nonnegative_number(minimum_order_qty) or 0
    multiple = _nonnegative_number(order_multiple) or 0
    quantity = max(ceil(needed), minimum)
    if multiple:
        quantity = ceil(quantity / multiple) * multiple
    return ceil(quantity)


def calculate_urgency(forecast_demand, current_stock, in_transit, lead_time_days):
    """Coverage-based priority, independent of quantity and data-review status.

    Transit arrival dates are unknown, so sufficient total inventory does not
    remove HIGH urgency when on-hand coverage is shorter than supplier lead time.
    Invalid inputs receive HIGH for investigation, not a claim of known shortage.
    """
    values = [_nonnegative_number(v) for v in
              (forecast_demand, current_stock, in_transit, lead_time_days)]
    result = {"current_coverage_days": None, "position_coverage_days": None}
    if any(v is None for v in values):
        return {**result, "urgency": "HIGH", "urgency_reason":
                "Coverage cannot be calculated from invalid demand, inventory or lead time. "
                "HIGH means manager investigation is needed; a shortage is not confirmed."}
    demand, stock, transit, lead = values
    if demand == 0:
        return {**result, "urgency": "LOW", "urgency_reason":
                "Forecast demand is zero, so no demand-driven depletion is projected; coverage days are not applicable."}
    daily = demand / 30
    current, position = stock / daily, (stock + transit) / daily
    result.update(current_coverage_days=current, position_coverage_days=position)
    evidence = (f"Current stock covers {current:.2f} days and stock plus transit covers "
                f"{position:.2f} days at {daily:.2f} units/day; supplier lead time is {lead:g} days. ")
    if stock == 0 or position < lead:
        level = "CRITICAL"
        reason = ("No stock is available now despite positive forecast demand." if stock == 0
                  else "Even stock plus transit cannot cover supplier lead time.")
    elif current < lead:
        level, reason = "HIGH", "On-hand stock runs out before lead time; verify the timing of in-transit deliveries."
    elif position < lead + 7:
        level, reason = "MEDIUM", "Total coverage meets lead time but has less than seven additional days of buffer."
    else:
        level, reason = "LOW", "On-hand coverage meets lead time and total coverage includes at least seven extra days."
    return {**result, "urgency": level, "urgency_reason": evidence + reason}


def calculate_replenishment(
    forecasts: pd.DataFrame,
    audit_history: pd.DataFrame,
    service_level_factor: float = 1.65,
    variability_months: int = 12,
) -> pd.DataFrame:
    """Use Phase 1 forecasts and its retained, flagged monthly history.

    Daily demand = monthly forecast / 30. With at least three clean monthly
    non-stockout observations, use sample standard deviation (ddof=1) of sales.
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
        for key in ["document_anomalies_detected", "document_anomalies_unreconciled", "possible_stockout_periods", "missing_history_months"]:
            if row.get(key, 0):
                reasons.append(key)
        if row.get("lead_time_source") == "manager_planning_assumption":
            reasons.append("manager_planning_lead_time")
        minimum = _nonnegative_number(row.get("minimum_order_qty"))
        multiple = _nonnegative_number(row.get("order_multiple"))
        row.update(minimum_order_qty=minimum, order_multiple=multiple,
                   moq_status="provided" if (minimum or multiple) else "unavailable", raw_required_qty=None)
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
        stockouts = recent.get("is_stockout", pd.Series(False, index=recent.index)).astype(bool)
        # Censored sales and imputed demand are not independent variability evidence.
        clean = recent.loc[~recent["is_anomaly"].astype(bool) & ~stockouts]
        if row.get("stockout_periods", 0):
            reasons.append("stockout_demand_estimated")
        if row.get("stockout_unresolved", 0):
            reasons.append("unresolved_stockout_demand")
        if row.get("stockout_fallback_periods", 0):
            reasons.append("stockout_estimation_fallback")
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
            "stockout_observations_excluded": int(stockouts.sum()),
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
                row["raw_required_qty"] = max(0, ceil(row["raw_order_qty"]))
                row["recommended_order_qty"] = apply_order_constraints(row["raw_required_qty"], minimum, multiple)
                if price is not None:
                    row["estimated_order_value"] = row["recommended_order_qty"] * price
            else:
                reasons.append("nonfinite_calculation")
        row["review_reasons"] = ";".join(reasons)
        row["status"] = "REVIEW" if reasons else (
            "ORDER" if row["recommended_order_qty"] > 0 else "COVERED"
        )
        row.update(calculate_urgency(row.get("forecast_demand"), row.get("current_stock"),
                                     row.get("in_transit"), row.get("lead_time_days")))
        rows.append(row)
    result = pd.DataFrame(rows, columns=list(dict.fromkeys([*forecasts.columns, *CALCULATED_COLUMNS])))
    result["recommended_order_qty"] = result["recommended_order_qty"].astype("Int64")
    return result


def supplier_proposals(recommendations: pd.DataFrame, quantity_column="recommended_order_qty"):
    """Return positive-quantity proposal lines and supplier totals, including REVIEW.

    Missing prices make the supplier's total value unknown, not understated.
    Zero-quantity and uncalculable recommendations stay in the main review table.
    """
    if quantity_column not in {"recommended_order_qty", "manager_order_qty"}:
        raise ValueError("Unsupported proposal quantity column")
    columns = ["supplier", "sku", "product_name", "recommended_order_qty",
               "unit_price", "estimated_order_value", "urgency", "urgency_reason", "status", "review_reasons"]
    columns += [c for c in ["raw_required_qty", "minimum_order_qty", "order_multiple", "moq_status", "explanation"] if c in recommendations]
    value_column = "estimated_order_value"
    if quantity_column == "manager_order_qty":
        columns += ["manager_order_qty", "manager_order_value", "quantity_changed", "decision_state", "approved_at"]
        value_column = "manager_order_value"
    lines = recommendations.loc[recommendations[quantity_column].gt(0).fillna(False), columns].copy()
    lines["supplier"] = lines["supplier"].fillna("Unknown supplier").replace(r"^\s*$", "Unknown supplier", regex=True)
    totals = []
    for supplier, group in lines.groupby("supplier", sort=True):
        totals.append({
            "supplier": supplier, "skus_to_order": len(group),
            "total_units": int(group[quantity_column].sum()),
            "estimated_order_value": group[value_column].sum(min_count=len(group)),
            "skus_requiring_review": int(group["status"].eq("REVIEW").sum()),
            "urgency": min(group["urgency"], key=URGENCY_RANK.get),
            "approved_skus": int(group["decision_state"].eq("Approved").sum()) if "decision_state" in group else 0,
        })
    return lines, pd.DataFrame(totals, columns=[
        "supplier", "skus_to_order", "total_units", "estimated_order_value", "skus_requiring_review", "urgency", "approved_skus",
    ])
