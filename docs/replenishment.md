# Phase 2: manager-reviewed replenishment

`calculate_replenishment(forecasts, audit_history, service_level_factor=1.65,
variability_months=12)` consumes both outputs of Phase 1's `forecast_demand`.
It returns a new dataframe and never sends orders or changes source data.

## Formula (units unless indicated)

- `daily_demand = forecast_demand / 30`
- `lead_time_demand = daily_demand * lead_time_days`
- `monthly_demand_std = sample standard deviation of clean monthly sales`
- `safety_stock = service_level_factor * monthly_demand_std * sqrt(lead_time_days / 30)`
- `target_stock = lead_time_demand + safety_stock`
- `inventory_position = current_stock + in_transit`
- `raw_order_qty = target_stock - inventory_position`
- `recommended_order_qty = max(0, ceil(raw_order_qty))`

Only the final quantity is rounded up. Display rounding never changes the calculation.
The monthly forecast already includes seasonality, stockout compensation and
sustainable growth. See [forecasting methods](demand_forecasting.md).

## Safety-stock assumptions and fallback

Use the latest 12 calendar months before the forecast month, independently per
SKU. Exclude Phase 1's `is_anomaly` and `is_stockout` observations without deleting
audit rows. Lost-demand estimates are not independent variability observations.
At least three clean observations are needed for sample standard deviation
(`ddof=1`). More variable monthly sales produce a larger buffer for the same
lead time and service factor. Seasonal variation in historical sales remains
in the variability estimate, so this baseline can be conservative.

The square-root conversion assumes independent, stationary daily demand:
monthly variance approximates 30 times daily variance. Therefore lead-time
standard deviation is monthly standard deviation times `sqrt(days / 30)`.
Monthly totals cannot reveal within-month variation; stockouts need the explicit
`stockout_days` metadata now included in the synthetic sample. This is an
explainable approximation, not a calibrated service guarantee. The configurable
factor defaults to 1.65; it is not a promised fill rate.

With fewer than three clean months, use
`monthly_demand_std = max(forecast_demand, clean historical mean)` (mean zero
when none exists). This assumes 100% monthly variability and requires REVIEW.
It is a conservative heuristic, not a guaranteed upper bound. Missing months
are unknown, never imputed as zero. Constant observed demand produces zero
estimated safety stock; this does not prove real-world demand is risk-free.

## Status and review

REVIEW takes precedence for estimated/unresolved stockout demand, detected sales anomalies, insufficient clean
history, missing months within the recent observed range, invalid numerical
inputs, or missing supplier/price. `review_reasons` contains machine-readable
reason codes. Flagged anomalies cannot inflate safety stock, but remain visible
for manager attention. Otherwise, positive quantity is ORDER and zero is COVERED.

Invalid forecast, lead time, stock or transit values produce a blank quantity,
not zero. Other REVIEW rows can retain provisional calculated quantities.
Dashboard order metrics include provisional positive quantities; COVERED counts
only confirmed COVERED rows. Review counts can therefore overlap order counts.

## Supplier proposals and inventory assumptions

Supplier lines include all positive quantities with their status and review
reasons. Value is quantity times the latest historical `unit_price`. An unknown
price leaves the supplier's total value unknown instead of silently omitting it.
Assume one common currency. Taxes, transport, pack sizes, supplier minimum order
quantities and capacity constraints are outside this phase.

Current stock and transit come from Phase 1's latest snapshot. All transit is
assumed usable and arriving within the lead-time horizon; no arrival dates are
available. The forecast month's snapshot is used rather than wall-clock dates,
which permits the historical sample to run reproducibly. Constant lead times,
30-day months and a lead-time-only replenishment horizon are assumed; there is
no additional periodic-review interval. The final decision remains with the
manager; there are no supplier communication or order-submission actions.

## Verification

Run all Phase 1 and Phase 2 checks with:

```powershell
python -m unittest discover -s tests -v
```
