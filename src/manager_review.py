"""In-memory demo review decisions. No filesystem, network or supplier actions."""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite

import pandas as pd


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event(record, action):
    record["history"].append({
        "timestamp": _timestamp(), "action": action,
        "calculated_quantity": record["original_order_qty"],
        "manager_quantity": record["manager_order_qty"],
        "calculation_id": record["calculation_id"],
    })


def sync_review_state(recommendations: pd.DataFrame, state: dict) -> dict:
    """Keep edits on rerun; invalidate approval when any calculation input changes.

    Untouched quantities follow updated calculations. Explicit overrides survive
    recalculation and need reapproval. Removed SKUs cannot appear in current views.
    """
    result = deepcopy(state)
    for _, row in recommendations.iterrows():
        sku = row["sku"]
        fingerprint = sha256(row.to_json(date_format="iso", double_precision=15).encode()).hexdigest()
        quantity = None if pd.isna(row["recommended_order_qty"]) else int(row["recommended_order_qty"])
        if sku not in result:
            result[sku] = {
                "calculation_id": fingerprint, "original_order_qty": quantity,
                "manager_order_qty": quantity, "decision_state": "Pending",
                "approved_at": None, "approved_quantity": None,
                "calculation_changed": False, "history": [],
            }
        elif result[sku]["calculation_id"] != fingerprint:
            record = result[sku]
            changed_by_manager = record["manager_order_qty"] != record["original_order_qty"]
            record.update(calculation_id=fingerprint, original_order_qty=quantity,
                          decision_state="Pending", approved_at=None, approved_quantity=None,
                          calculation_changed=True)
            if not changed_by_manager:
                record["manager_order_qty"] = quantity
            _event(record, "calculation_changed")
    return result


def set_review_quantity(state: dict, sku: str, quantity) -> dict:
    """An edit always invalidates an existing approval; original stays immutable."""
    try:
        number = float(quantity)
    except (TypeError, ValueError):
        raise ValueError("Manager quantity must be a nonnegative whole number") from None
    if isinstance(quantity, bool) or not isfinite(number) or number < 0 or not number.is_integer():
        raise ValueError("Manager quantity must be a nonnegative whole number")
    result = deepcopy(state)
    record = result[sku]
    if record["manager_order_qty"] != int(number):
        record.update(manager_order_qty=int(number), decision_state="Pending",
                      approved_at=None, approved_quantity=None)
        _event(record, "quantity_changed")
    return result


def approve_review(state: dict, sku: str) -> dict:
    """Approve this SKU's reviewed quantity locally; approving zero is valid."""
    result = deepcopy(state)
    record = result[sku]
    if record["manager_order_qty"] is None:
        raise ValueError("Set a reviewed quantity before approving")
    if record["decision_state"] != "Approved":
        record.update(decision_state="Approved", approved_at=_timestamp(),
                      approved_quantity=record["manager_order_qty"], calculation_changed=False)
        _event(record, "approved_demo_only")
    return result


def reviewed_recommendations(recommendations: pd.DataFrame, state: dict) -> pd.DataFrame:
    """Append review fields without changing calculated recommendation/value."""
    result = recommendations.copy(deep=True)
    records = [state[sku] for sku in result["sku"]]
    result["manager_order_qty"] = pd.array([r["manager_order_qty"] for r in records], dtype="Int64")
    result["quantity_changed"] = [r["manager_order_qty"] != r["original_order_qty"] for r in records]
    result["decision_state"] = [r["decision_state"] for r in records]
    result["approved_at"] = [r["approved_at"] for r in records]
    result["manager_order_value"] = result["manager_order_qty"].astype(float) * pd.to_numeric(result["unit_price"], errors="coerce")
    return result
