# Stockout compensation and sustainable growth

## Inspection findings

The previous version contained per-SKU median/MAD spike detection, recent median
demand, optional SKU/category seasonality, and lead-time replenishment with safety
stock. It had neither historical stockout metadata nor lost-demand compensation,
and no sustained-growth estimator. `current_stock` is a current snapshot, not a
historical availability record; it cannot establish past stockouts.

## Compatible data extension

`stockout_days` is an optional integer from zero to the calendar month's length.
The bundled synthetic CSV now explicitly records zero for available months and:

| SKU | Month | Stockout days | Preserved observed sales |
| --- | --- | ---: | ---: |
| PAN-310 | April 2025 | 28 | 12 |
| HOU-040 | September 2025 | 30 | 0 |
| SNK-440 | March 2026 | 29 | 24 |

These annotations are synthetic scenario definitions, not inferred historical
facts. All existing columns and rows remain. BEV-220's January–June 2026 sales
were changed to 220, 250, 285, 325, 365 and 410 to represent sustained adoption
of the cold brew product. Other observed sales values remain unchanged.

Legacy data without the field still works. Missing values mean availability is
unknown; they trigger no invented stockout. The audit records
`stockout_data_available`, and explanations disclose missing metadata. Zero
sales without stockout evidence remains a valid demand observation. Negative,
fractional or excessive stockout days and positive sales during a full-month
stockout are rejected.

## Lost-demand estimate

For each SKU and stockout period, find non-stockout, non-anomalous comparators.
Prefer the same calendar month if at least two observations exist; otherwise
use up to six nearest months within 12 months on either side. This is a
retrospective reconstruction using only the supplied history; when evaluating
a past forecast, supply only history available at that forecast origin.

```text
comparable_daily_rate = median(comparator_sales / comparator_calendar_days)
estimated_lost_demand = comparable_daily_rate × stockout_days
adjusted_demand = observed_sales + estimated_lost_demand
```

Raw sales and all anomaly flags remain available. Comparators never use imputed
demand. One or two nearby comparators are marked as limited evidence. With none,
at least seven available days permit `sales / available_days` as a flagged
fallback. Otherwise lost demand is unknown, and the period is excluded from the
baseline rather than interpreted as zero. If no usable demand history remains,
the forecast and proposed quantity are blank and the SKU requires REVIEW.
The method assumes comparable demand across available and unavailable days; it
cannot recover within-month promotions or daily patterns from monthly totals.

The recent baseline is the median of non-spike adjusted demand in the latest six
calendar months, with the existing older-history fallback. Compensating an old
or isolated outage may leave this robust median unchanged; lost units are not
blindly added to next month's order. The summary separately shows historical
lost units and the actual change in baseline.

Anomaly thresholds exclude known stockout periods. A suspected global spike must
also exceed the same median/MAD (IQR fallback) threshold computed from available
observations within four months, when at least four such observations exist.
This prevents a long flat history from classifying a sustained new level as bulk
orders while retaining isolated-spike detection.

## Sustainable growth

Use the latest six non-anomalous, non-stockout observations within nine calendar
months. The newest must be within two months of the forecast month. Imputed
demand never establishes growth. Divide observations by the learned calendar-month
seasonal index when a reliable profile exists, so known seasonal increases are
not counted again as growth. Without enough seasonal history, the detection is
a conservative trend heuristic and cannot prove a seasonal ramp is permanent.

Require both:

- At least four of five successive changes are positive (exceeding 0.1% of the
  first-three median, or a numerical tolerance).
- The last-three median exceeds or equals 110% of the first-three median,
  and the first-three median is positive.

Calculate a robust Theil-Sen slope as the median of all pairwise demand changes
divided by elapsed calendar months. Fit the intercept as the median residual,
project to the next month, and restore that month's seasonal index.

```text
seasonal_forecast = stockout_adjusted_baseline × seasonal_factor
growth_factor = min(1.5, max(1, projected_demand / seasonal_forecast))
final_forecast = seasonal_forecast × growth_factor
```

If evidence fails the persistence checks, or projection adds no demand, the
growth factor is 1. The 50% cap is a conservative extrapolation guardrail.
Neither one positive jump nor one isolated bulk order establishes a trend.

## Explanations and replenishment

The UI's calculation table reconciles exactly:

```text
raw recent mean
  + robust/spike adjustment
  + stockout adjustment
  + seasonal adjustment
  + growth adjustment
  = final forecast
```

The robust/spike adjustment includes the switch from mean to median, not just
removal of flagged spikes. The baseline column contains the median after
stockout compensation. All adjustment factors, evidence counts and method
reasons are returned with the forecast. Historical charts retain observed sales
and separately display reconstructed demand and stockout markers.

The existing replenishment formula consumes the final forecast without applying
any adjustment a second time. Stockout rows, like anomalies, are excluded from
safety-stock variability. Stockout estimates require REVIEW; unreliable estimates
receive additional reason codes. Supplier proposals remain manager-reviewed,
with no automatic sending or order-submission capability.
