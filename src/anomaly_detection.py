"""Auditable detection of unusually high monthly sales, independently per SKU."""

import numpy as np
import pandas as pd


def detect_sales_anomalies(data: pd.DataFrame) -> pd.DataFrame:
    """Keep every row; flag high sales using median/MAD, with IQR fallback.

    Require four observations. Threshold is median + max(4 robust standard
    deviations, 50% of median, 1 unit). The floor handles constant history.
    Input has one finite, nonnegative sales observation per SKU/month.
    """
    result = data.copy()
    result["date"] = pd.to_datetime(result["date"], errors="raise")
    result["sales"] = pd.to_numeric(result["sales"], errors="raise")
    if result[["sku", "date", "sales"]].isna().any().any():
        raise ValueError("SKU, date and sales must not be missing")
    if not np.isfinite(result["sales"]).all() or (result["sales"] < 0).any():
        raise ValueError("Sales must be finite and nonnegative")
    if result.assign(_month=result["date"].dt.to_period("M")).duplicated(["sku", "_month"]).any():
        raise ValueError("Expected one sales observation per SKU/month")
    result = result.sort_values(["sku", "date"]).reset_index(drop=True)
    result["is_anomaly"] = False
    result["anomaly_reason"] = ""
    result["anomaly_threshold"] = np.nan
    for _, group in result.groupby("sku", sort=False):
        if len(group) < 4:
            continue
        sales = group["sales"]
        median = sales.median()
        scale = 1.4826 * (sales - median).abs().median()
        if scale == 0:
            scale = (sales.quantile(0.75) - sales.quantile(0.25)) / 1.349
        threshold = median + max(4 * scale, 0.5 * median, 1.0)
        result.loc[group.index, "anomaly_threshold"] = threshold
        spikes = group.index[sales > threshold]
        result.loc[spikes, "is_anomaly"] = True
        result.loc[spikes, "anomaly_reason"] = "high_sales_spike"
    return result
