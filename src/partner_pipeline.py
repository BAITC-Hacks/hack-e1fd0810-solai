"""Partner normalization boundary around the existing monthly demand engine."""

import numpy as np
import pandas as pd

from src import forecasting


def partner_forecasts(partner, skus=None, planning_lead_days=None):
    """Forecast complete, observed months only; leave unknown inputs unknown.

    The optional lead time is an explicit manager scenario, never partner data.
    Filtering before forecasting makes paginated dashboard reruns inexpensive.
    """
    catalog = partner.catalog.copy()
    if skus is not None:
        catalog = catalog.loc[catalog.sku.isin(skus)].copy()
    if planning_lead_days is not None:
        if not np.isfinite(planning_lead_days) or planning_lead_days < 0:
            raise ValueError("Planning lead time must be finite and nonnegative")
        catalog["lead_time_days"] = float(planning_lead_days)
        catalog["lead_time_source"] = "manager_planning_assumption"
    history = partner.history.loc[partner.history.sku.isin(catalog.sku)].copy()
    history = history.loc[~history.partial_month & history.sales.notna() & history.sales.ge(0)]
    history = history.drop(columns=["brand"]).merge(catalog, on="sku", validate="many_to_one")
    target = partner.as_of.to_period("M").to_timestamp()
    if len(history):
        forecasts, audit = forecasting.forecast_demand(history, seasonal_profiles=partner.seasonality, forecast_month=target)
    else:
        forecasts = pd.DataFrame(columns=forecasting.SUMMARY_COLUMNS)
        audit = history.assign(is_anomaly=False, is_stockout=False, adjusted_demand=pd.Series(dtype=float))
    missing = catalog.loc[~catalog.sku.isin(forecasts.sku)]
    rows = []
    for _, item in missing.iterrows():
        row = {column: np.nan for column in forecasting.SUMMARY_COLUMNS}
        row.update(item.to_dict())
        row.update(forecast_month=target, seasonal_source="none", seasonal_factor=1.0,
                   growth_factor=1.0, growth_detected=False, growth_reason="no_usable_history")
        for key in ["anomalies_detected", "baseline_observations", "baseline_anomalies_excluded",
                    "stockout_periods", "stockout_unresolved", "stockout_fallback_periods",
                    "stockout_metadata_missing", "growth_observations", "estimated_lost_demand"]:
            row[key] = 0
        rows.append({k: row[k] for k in forecasting.SUMMARY_COLUMNS})
    if rows:
        forecasts = pd.concat([forecasts, pd.DataFrame(rows)], ignore_index=True)
    extra = [c for c in catalog.columns if c not in forecasts.columns]
    forecasts = forecasts.merge(catalog[["sku", *extra]], on="sku", validate="one_to_one")
    for column in ["document_anomalies_detected", "document_anomalies_unreconciled", "document_excluded_qty", "possible_stockout"]:
        counts = history.groupby("sku")[column].sum()
        name = "possible_stockout_periods" if column == "possible_stockout" else column
        forecasts[name] = forecasts.sku.map(counts).fillna(0)
    forecasts["missing_history_months"] = forecasts.sku.map(
        partner.history.loc[~partner.history.partial_month].groupby("sku").sales.apply(lambda s: int(s.isna().sum()))
    )
    return forecasts.sort_values("sku").reset_index(drop=True), audit
