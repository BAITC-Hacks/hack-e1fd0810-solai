"""Auditable detection of unusually high monthly sales, independently per SKU."""

import numpy as np
import pandas as pd

from src.demand_adjustments import prepare_stockout_history


def _spike_threshold(sales):
    median = sales.median()
    scale = 1.4826 * (sales - median).abs().median()
    if scale == 0:
        scale = (sales.quantile(0.75) - sales.quantile(0.25)) / 1.349
    return median + max(4 * scale, 0.5 * median, 1.0)


def detect_sales_anomalies(data: pd.DataFrame) -> pd.DataFrame:
    """Keep every row; flag high sales using median/MAD, with IQR fallback.

    Require four observations. Threshold is median + max(4 robust standard
    deviations, 50% of median, 1 unit). The floor handles constant history.
    Stockout rows do not establish the threshold. Suspected spikes must also
    exceed a local threshold within four months when four observations exist.
    Input has one finite, nonnegative sales observation per SKU/month.
    """
    result = data.copy()
    result["date"] = pd.to_datetime(result["date"], errors="raise")
    result["sales"] = pd.to_numeric(result["sales"], errors="raise")
    if result[["sku", "date", "sales"]].isna().any().any():
        raise ValueError("SKU, date and sales must not be missing")
    if not np.isfinite(result["sales"]).all() or (result["sales"] < 0).any():
        raise ValueError("Sales must be finite and nonnegative")
    result = prepare_stockout_history(result)
    if result.assign(_month=result["date"].dt.to_period("M")).duplicated(["sku", "_month"]).any():
        raise ValueError("Expected one sales observation per SKU/month")
    result = result.sort_values(["sku", "date"]).reset_index(drop=True)
    result["is_anomaly"] = False
    result["anomaly_reason"] = ""
    result["anomaly_threshold"] = np.nan
    for _, group in result.groupby("sku", sort=False):
        reference = group.loc[~group["is_stockout"], "sales"]
        if len(reference) < 4:
            continue
        sales = group["sales"]
        threshold = _spike_threshold(reference)
        result.loc[group.index, "anomaly_threshold"] = threshold
        # A long flat history must not turn a sustained new level into spikes.
        # Suspected spikes must also be unusual relative to nearby observations.
        months = group["date"].dt.to_period("M").astype("int64")
        for index in group.index[sales > threshold]:
            local = group.loc[(months - months.loc[index]).abs().le(4) & ~group["is_stockout"], "sales"]
            if len(local) >= 4:
                result.loc[index, "anomaly_threshold"] = max(threshold, _spike_threshold(local))
        spikes = group.index[sales > result.loc[group.index, "anomaly_threshold"]]
        result.loc[spikes, "is_anomaly"] = True
        result.loc[spikes, "anomaly_reason"] = "high_sales_spike"
    return result
