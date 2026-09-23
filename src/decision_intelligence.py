"""Read-only decision intelligence over engine outputs; no forecast/order formulas.

All public functions are pure. Unknown numeric inputs stay unknown. Quality labels
are evidence rules, never probabilities. Priority is a lexicographic ordering,
not an opaque score. No function approves, edits, or sends an order.
"""

from hashlib import sha256
from math import isfinite

import pandas as pd

from src.replenishment import URGENCY_RANK


def number(value):
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (TypeError, ValueError):
        return None


def format_number(value, unavailable="Not available"):
    value = number(value)
    return unavailable if value is None else f"{value:,.2f}".rstrip("0").rstrip(".")


def numeric(frame, key):
    return pd.to_numeric(frame.get(key, pd.Series(index=frame.index, dtype=float)), errors="coerce").replace([float("inf"), -float("inf")], float("nan"))


def filter_recommendations(frame, search="", supplier=None, urgencies=(), statuses=()):
    result = frame.copy()
    if supplier:
        result = result.loc[result.supplier.eq(supplier)]
    if search.strip():
        match = result.sku.astype(str).str.contains(search.strip(), case=False, regex=False)
        match |= result.product_name.fillna("").str.contains(search.strip(), case=False, regex=False)
        result = result.loc[match]
    if urgencies:
        result = result.loc[result.urgency.isin(urgencies)]
    if statuses:
        result = result.loc[result.status.isin(statuses)]
    return result.copy()


def portfolio_metrics(frame):
    qty = numeric(frame, "recommended_order_qty")
    positive = qty.gt(0)
    review = frame.get("status", pd.Series(index=frame.index, dtype=str)).eq("REVIEW")
    urgent = frame.get("urgency", pd.Series(index=frame.index, dtype=str)).isin(["CRITICAL", "HIGH"])
    return {
        "skus": len(frame), "requiring_order": int(positive.sum()),
        "requiring_action": int((positive | review | urgent).sum()),
        "critical_high": int(urgent.sum()), "recommended_units": number(qty.sum(min_count=1)),
        "unknown_orders": int(qty.isna().sum()), "review_count": int(review.sum()),
        "suppliers_affected": int(frame.loc[positive, "supplier"].nunique()) if "supplier" in frame else 0,
    }


def data_quality_summary(frame):
    """Counts are affected SKUs, not summed missing periods or invented values."""
    counts = {}
    for column in ["forecast_demand", "lead_time_days", "current_stock", "in_transit", "unit_price"]:
        n = int(numeric(frame, column).isna().sum())
        if n:
            counts[f"missing_{column}"] = n
    for column in ["missing_history_months", "stockout_metadata_missing", "stockout_unresolved",
                   "document_anomalies_unreconciled"]:
        n = int(numeric(frame, column).gt(0).sum())
        if n:
            counts[column] = n
    checks = {
        "insufficient_history": numeric(frame, "history_observations").lt(3),
        "seasonality_unavailable": frame.get("seasonal_source", pd.Series(index=frame.index, dtype=str)).eq("none"),
        "planning_lead_time": frame.get("lead_time_source", pd.Series(index=frame.index, dtype=str)).eq("manager_planning_assumption"),
        "anomalies_flagged": numeric(frame, "anomalies_detected").gt(0) | numeric(frame, "document_anomalies_detected").gt(0),
    }
    for code, mask in checks.items():
        if mask.any():
            counts[code] = int(mask.sum())
    return counts


def quality_label(row):
    critical = ["forecast_demand", "lead_time_days", "current_stock", "in_transit", "recommended_order_qty"]
    missing = [key for key in critical if number(row.get(key)) is None]
    reasons = [v for v in str(row.get("review_reasons", "")).split(";") if v and v != "nan"]
    if missing or reasons or row.get("status") == "REVIEW":
        return "review_required", sorted(set([*missing, *reasons]))
    limited = []
    for key, minimum in [("baseline_observations", 6), ("history_observations", 12)]:
        value = number(row.get(key))
        if value is None or value < minimum:
            limited.append(key)
    if row.get("seasonal_source", "none") == "none":
        limited.append("seasonality_unavailable")
    if (number(row.get("stockout_metadata_missing")) or 0) > 0:
        limited.append("stockout_metadata_missing")
    return ("limited_data", limited) if limited else ("strong_data", [])


def priority_queue(frame):
    """Urgency, review flag, shortest known coverage, order requirement, then SKU.

    Unknown coverage sorts after known coverage within the same urgency/review
    band. Existing urgency already marks missing critical inputs HIGH to investigate.
    """
    result = frame.copy()
    result["_urgency"] = result.urgency.map(URGENCY_RANK).fillna(len(URGENCY_RANK))
    result["_review"] = result.status.eq("REVIEW")
    result["_coverage"] = numeric(result, "current_coverage_days")
    result["_order"] = numeric(result, "recommended_order_qty").gt(0)
    result = result.sort_values(["_urgency", "_review", "_coverage", "_order", "sku"],
                                ascending=[True, False, True, False, True], na_position="last", kind="stable")
    result["priority_reason"] = result.apply(lambda row:
        f"{row.urgency}: {row.get('urgency_reason', '')} "
        + ("Data review is required. " if row.status == "REVIEW" else "")
        + f"Calculated order: {format_number(row.get('recommended_order_qty'))}.", axis=1) if len(result) else pd.Series(dtype=str)
    return result.drop(columns=["_urgency", "_review", "_coverage", "_order"]).reset_index(drop=True)


def key_signals(row, history=None):
    """Structured evidence only. Signals never modify the calculated forecast."""
    signals = []
    def add(code, **values):
        signals.append({"code": code, **values})
    if number(row.get("growth_detected")) == 1:
        factor = number(row.get("growth_factor"))
        add("growth", percent=(factor - 1) * 100 if factor is not None else None)
    excluded = (number(row.get("baseline_anomalies_excluded")) or 0)
    documents = number(row.get("document_anomalies_detected")) or 0
    if excluded or documents:
        add("spikes", monthly=excluded, documents=documents)
    if (number(row.get("stockout_periods")) or 0) > 0:
        add("stockout", periods=number(row.get("stockout_periods")), lost=number(row.get("estimated_lost_demand")))
    stock, lead = number(row.get("current_stock")), number(row.get("lead_time_demand"))
    if stock is not None and lead is not None and stock < lead:
        add("lead_exposure", stock=stock, lead=lead)
    position, target = number(row.get("inventory_position")), number(row.get("target_stock"))
    if position is not None and target is not None and target > 0 and position > 2 * target:
        add("above_target", position=position, target=target)
    raw, final = number(row.get("raw_required_qty")), number(row.get("recommended_order_qty"))
    if raw is not None and final is not None and final > raw:
        add("constraint", raw=raw, final=final)
    n = number(row.get("history_observations"))
    if n is None or n < 3:
        add("history", observations=n)
    if history is not None and len(history):
        clean = history.loc[~history.is_anomaly.astype(bool) & ~history.is_stockout.astype(bool)].sort_values("date")
        target_month = pd.Timestamp(row["forecast_month"]).to_period("M")
        clean = clean.loc[(clean.date.dt.to_period("M") < target_month)
                          & (clean.date.dt.to_period("M") >= target_month - 9)].tail(6)
        if len(clean) == 6 and clean.date.dt.to_period("M").astype("int64").diff().dropna().eq(1).all():
            values = pd.to_numeric(clean.sales)
            first, last = values.iloc[:3].median(), values.iloc[-3:].median()
            if first > 0 and last <= .9 * first and values.diff().lt(0).sum() >= 4:
                add("sales_decline", percent=(1 - last / first) * 100)
    return signals


def executive_brief(frame):
    """JSON-safe facts for the CURRENT filtered set, independent of pagination."""
    metrics = portfolio_metrics(frame)
    queue = priority_queue(frame)
    attention = queue.loc[queue.status.eq("REVIEW") | queue.urgency.isin(["HIGH", "CRITICAL"])
                          | numeric(queue, "recommended_order_qty").gt(0)].head(5)
    positive = frame.loc[numeric(frame, "recommended_order_qty").gt(0)].copy()
    suppliers = []
    for supplier, group in positive.groupby("supplier", dropna=False, sort=True):
        suppliers.append({"supplier": None if pd.isna(supplier) else str(supplier),
                          "skus": len(group), "units": number(numeric(group, "recommended_order_qty").sum(min_count=1))})
    actions = []
    if metrics["unknown_orders"]:
        actions.append("confirm_missing_inputs")
    if metrics["critical_high"]:
        actions.append("review_urgent")
    if metrics["requiring_order"]:
        actions.append("review_then_approve")
    if not actions:
        actions.append("review_data" if metrics["review_count"] else "monitor")
    return {"metrics": metrics, "attention": attention[["sku", "product_name", "urgency", "priority_reason"]].to_dict("records"),
            "suppliers": suppliers, "data_risks": data_quality_summary(frame), "actions": actions}


def sku_analysis(row, history=None):
    quality, reasons = quality_label(row)
    fields = ["raw_demand_estimate", "baseline_demand", "anomaly_adjustment", "stockout_adjustment",
              "estimated_lost_demand", "seasonal_factor", "seasonal_adjustment", "growth_factor", "growth_adjustment",
              "forecast_demand", "current_stock", "in_transit", "inventory_position", "current_coverage_days",
              "position_coverage_days", "lead_time_days", "lead_time_demand", "safety_stock", "target_stock",
              "raw_required_qty", "minimum_order_qty", "order_multiple", "recommended_order_qty"]
    return {"sku": str(row["sku"]), "values": {key: number(row.get(key)) for key in fields},
            "quality": quality, "quality_reasons": reasons, "urgency": str(row.get("urgency", "")),
            "urgency_reason": str(row.get("urgency_reason", "")), "signals": key_signals(row, history),
            "next_action": "confirm_missing_inputs" if number(row.get("recommended_order_qty")) is None
            else "review_then_approve" if (number(row.get("recommended_order_qty")) or 0) > 0
            else "review_data" if quality == "review_required" else "monitor"}


def context_fingerprint(frame, language, scope=""):
    payload = frame.sort_values("sku").to_json(date_format="iso", double_precision=15)
    return sha256((payload + language + scope).encode()).hexdigest()
