# Solai Warehouse Replenishment MVP

A HackAlem demo that forecasts monthly SKU demand and proposes warehouse replenishment. Managers can inspect anomaly, stockout, seasonal and growth adjustments, review urgency, edit quantities and approve decisions. **Approval is a local demo decision: no supplier order is sent.**

## Installation, run and tests

Requires Python 3.10 or newer. Run these commands from the repository root:

```bash
python -m venv .venv
```

Activate in Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Or on macOS/Linux:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Exact application command:

```bash
python -m streamlit run app.py
```

Open the local URL printed by Streamlit, usually `http://localhost:8501`.

Exact command for the complete test suite:

```bash
python -m unittest discover -s tests -v
```

Tests use Python unittest and Streamlit AppTest; pytest is not required. They cover forecasting, stockouts, growth, anomalies, replenishment, urgency, supplier totals, schema recovery and manager editing/approval across reruns.

## Architecture

| File | Responsibility |
| --- | --- |
| `app.py` | Streamlit tables, Plotly charts, explanations, session-backed review and supplier views |
| `src/data_loader.py` | Synthetic CSV loading and basic validation |
| `src/partner_loader.py` | Validated Excel adapters, 1C joins, document anomaly audit and inventory evidence |
| `src/partner_pipeline.py` | Observed complete-month histories and explicit manager planning assumptions |
| `src/anomaly_detection.py` | Per-SKU spike flags, thresholds and audit reasons |
| `src/demand_adjustments.py` | Stockout metadata, lost-demand estimation and sustainable growth |
| `src/forecasting.py` | Recent baseline, SKU/category seasonality, final forecast and forecast explanations |
| `src/replenishment.py` | Safety stock, calculated quantities, urgency and supplier grouping |
| `src/manager_review.py` | Separate manager quantities, approval timestamps and in-memory decision history |
| `src/explanations.py` | Human-readable replenishment and urgency explanations |
| `src/engine.py` | Refresh legacy imported modules after result-schema changes |
| `data/sample_data.csv` | Synthetic monthly history and inventory inputs |
| `tests/` | Automated calculation and UI regression tests |

## Input data

The primary source is **12 partner-provided Excel workbooks in `data/raw/`**, covering IEK (3,185 distinct 1C codes) and Systeme Electric (724). All 14 sheets were inspected. See [the workbook inspection and mapping report](docs/partner_data_inspection.md) for exact filenames, headers, dimensions, joins and data-quality limits.

The loader preserves 1C codes as strings, including leading zeroes and trailing underscores. Monthly sales/inventory `Номенклатура.Код`, document `Код`, and transit/MOQ `Код 1с` (or `Номенклатура.Код`) join on the same real SKU. Articles/names are metadata, not fuzzy join keys. Brands supply the supplier grouping; they are not confirmed legal supplier entities. No customer identifiers are supplied.

Monthly reports cover January 2024 through September 2026. September is partial at the dated 22 September 2026 snapshot: it is retained for audit and excluded from fitting. Forecast origin is September 2026, not the computer's current date. Blank monthly cells remain unknown. Complete observed signed outbound document totals can fill a blank sales month; a month with missing document quantities remains incomplete; missing documents never imply zero sales. Negative net return months remain auditable but are excluded from demand fitting. Monthly and document sales are reconciled, never added together.

Current stock is the latest monthly inventory cell; for 497 Systeme Electric products, the dated `Свободный остаток` snapshot overrides it. Known shipment quantities are summed for transit. An all-blank transit entry stays unknown. No historical inventory blank is converted into zero. Warehouse-level documents and available stock components remain in normalized audit data, but inventory scope cannot consistently be allocated to the document warehouse, so recommendations filter by brand rather than claiming warehouse-specific coverage.

**Lead times and confirmed supplier purchase prices are absent.** Enter an explicit **Planning lead time (days)** in the sidebar to calculate provisional recommendations where stock/transit/history are known. Leaving it blank leaves order quantities unknown and marked REVIEW. Prices remain unavailable; `СС реал` is preserved as reported cost and is not used as a supplier quote. IEK category is absent; Systeme Electric category codes are preserved where supplied.

Choose **Synthetic demo** in the sidebar to use `data/sample_data.csv`. It remains a fallback when no partner workbooks exist and a test fixture; malformed/incomplete partner files surface a load error instead of silently switching data. The synthetic dataset has 144 observations, 8 SKUs, 4 categories and 18 months (January 2025 through June 2026). Columns are:

`date`, `sku`, `product_name`, `category`, `supplier`, `sales`, `current_stock`, `in_transit`, `lead_time_days`, `unit_price`, and optional `stockout_days`.

There must be one finite, nonnegative sales observation per SKU/calendar month. Inventory, lead time, supplier and price use the latest SKU snapshot. `stockout_days` records unavailable days during that historical month. Absent stockout metadata is unknown; low sales and current inventory do not independently establish a historical stockout. The synthetic dataset has no customer identities or transaction identifiers.

The sample includes three annotated outages and six growing months for cold brew coffee. Its history is too short for learned seasonality, so seasonal factors are 1.0. At default settings, inventory covers calculated targets and all calculated orders are zero. Tests include positive shortages and multi-year seasonality. Managers may enter a positive demo quantity without changing the sample or calculation.

## Forecasting methodology

Each SKU is sorted chronologically and processed independently. The raw estimate is the mean of recent sales. The baseline is the median of non-anomalous, stockout-adjusted demand in the latest six calendar months. If none is usable, up to six older usable observations supply the fallback. Missing months are not assumed to have zero demand.

```text
final_forecast = stockout_adjusted_baseline * seasonal_factor * growth_factor
```

The UI exposes the raw-sales baseline, robust/spike adjustment, stockout adjustment, seasonal adjustment, growth adjustment and final forecast. The robust/spike adjustment includes the switch from mean to median, not only removal of flagged spikes.

### Anomaly/outlier detection and exclusion

From non-stockout observations, calculate median sales and MAD (median absolute deviation). Robust standard deviation is `1.4826 * MAD`, falling back to `IQR / 1.349` when MAD is zero.

```text
spike_threshold = median + max(4 * robust_std, 0.5 * median, 1 unit)
```

At least four available observations are required. A suspected global spike must also exceed the equivalent local threshold within four months when at least four nearby observations exist. This helps distinguish a sustained new level from a one-off bulk order. Sales strictly above the threshold are flagged `is_anomaly=True`, with reason `high_sales_spike`.

Rows are retained for audit, but flagged sales are excluded from regular demand, growth evidence, lost-demand comparators and safety-stock variability. Customer-specific bulk purchases cannot be identified without customer IDs.

For partner data, signed outbound lines first aggregate per SKU/document/warehouse. With at least eight positive documents, a candidate must exceed all of: `median + 6 * robust_std`, `5 * median`, and 10 units. A comparable repeated purchase (at least 80% of the candidate quantity) prevents automatic one-off exclusion. Only isolated candidates whose monthly signed shipment total reconciles exactly with the monthly report (tolerance 0.000001), and whose quantity does not exceed that net total, are subtracted. Unreconciled candidates are retained as review flags. Original quantities, document identifiers, reasons, thresholds, raw monthly values and excluded quantities remain auditable. This is a conservative statistical classification, not confirmation of a customer's intent. The optional future anonymized `customer_id` can be retained by the detector; there is no current customer-level attribution or anonymization service.

### Seasonality

Partner forecasts first use the supplied 12-month **brand-level** `СЕЗОННОСТЬ` coefficient profile (source `partner_brand`). These coefficients are normalized relative to the baseline months using the formula below; they are not treated as SKU-specific measurements. If a usable mapped profile is unavailable, derive history-based seasonality:

Require two complete calendar years of non-spike, estimable demand. Normalize each year's months by its annual median, then take the median index for each calendar month. Eligible same-category peers provide a normalized profile when the SKU lacks one. Without sufficient evidence, use factor 1.0.

```text
seasonal_factor = next_month_index / median_index_of_baseline_months
```

This adjusts relative to the recent season rather than counting seasonality twice.

### Sustainable growth

Use the latest six clean, non-stockout observations within nine months, with the newest no more than two months before the forecast month. Remove known seasonality first. Require at least four of five positive transitions and at least a 10% increase between the first-three and last-three medians. Positive changes must exceed 0.1% of the first-three median or a numerical tolerance.

Estimate a Theil-Sen slope (median of pairwise demand changes per elapsed month), fit a median residual intercept, and project next-month demand. Restore the next month's seasonal index.

```text
growth_factor = min(1.5, max(1, projected_demand / seasonal_forecast))
```

Without persistent evidence, the factor is 1.0. One isolated bulk order cannot establish a trend. The 50% uplift cap limits extrapolation.

### Stockout/lost-demand compensation

Prefer two or more same-calendar-month comparators; otherwise use up to six nearest non-stockout, non-anomalous months within 12 months of the outage.

```text
comparable_daily_rate = median(comparator_sales / comparator_calendar_days)
estimated_lost_demand = comparable_daily_rate * stockout_days
adjusted_demand = observed_sales + estimated_lost_demand
```

Imputed demand never supplies comparator rates or growth evidence. One or two nearby comparators are flagged as limited evidence. With none, at least seven available days permit an own-sales/available-days fallback; otherwise demand is unknown. If no usable history remains, forecast and calculated order are blank and require REVIEW. Raw sales remain unchanged in the audit. Historical lost units are not blindly added to next month's order.

For partner data, absent outage-day metadata does not establish availability or an outage. Only an **explicit zero historical inventory snapshot** supports a possible-stockout flag. With comparable clean months, estimate `lost = max(0, comparable_daily_rate * calendar_days - observed_sales)`, leaving the outage duration unknown. Such estimates require review and are excluded from growth/variability evidence. Without comparators they are unresolved, not zero. The supplied historical inventory sheets contain **no explicit zeros**, so this import infers no historical stockouts; current zero free stock alone does not establish a historical period outage.

## Replenishment formula and safety stock

```text
daily_demand = final_forecast / 30
lead_time_demand = daily_demand * lead_time_days
safety_stock = service_factor * monthly_std * sqrt(lead_time_days / 30)
target_stock = lead_time_demand + safety_stock
inventory_position = current_stock + in_transit
raw_order_qty = target_stock - inventory_position
raw_required_qty = max(0, ceil(raw_order_qty))
# If raw_required_qty == 0, final quantity remains 0.
quantity = max(raw_required_qty, supplied_minimum_or_zero)
recommended_order_qty = ceil(quantity / order_multiple) * order_multiple
# Without a positive order multiple, use ceil(quantity).
```

The configurable service factor defaults to 1.65. `monthly_std` is sample standard deviation (`ddof=1`) from up to 12 recent months, excluding anomalies and stockouts. At least three clean observations are needed; otherwise use the greater of forecast demand and clean historical mean as a conservative 100% variability proxy and require REVIEW. The whole-unit raw requirement and final constrained quantity are both retained. IEK `Мин. разр. к отгр.` is a **minimum shipment**, not an assumed multiple. Systeme Electric `Кратность` is an **order multiple**, with the dedicated MOQ workbook taking precedence over embedded sales-sheet values. Example: requirement 137 with multiple 20 becomes 140; requirement 21 with minimum 20 stays 21. Zero/absent constraints leave the ordinary requirement unchanged and are labelled unavailable. Never round downward or create an order solely because a minimum exists.

ORDER means positive calculated quantity; COVERED means inventory meets the target. REVIEW overrides either for anomalies, stockout estimates, insufficient/missing history or invalid data. Invalid quantity inputs produce a blank quantity, not zero coverage. Missing prices leave supplier values unknown.

## Urgency calculation

For positive demand:

```text
current_coverage_days = current_stock / daily_demand
position_coverage_days = (current_stock + in_transit) / daily_demand
```

Apply these rules in order using unrounded values:

| Level | Rule |
| --- | --- |
| CRITICAL | Current stock is zero, or total coverage is shorter than lead time |
| HIGH | Current-stock coverage is shorter than lead time, although transit makes total coverage sufficient |
| MEDIUM | Current stock covers lead time, but total coverage is less than lead time plus seven days |
| LOW | Current stock covers lead time, and total coverage includes at least seven extra days |

Zero forecast demand is LOW with coverage marked not applicable. Invalid demand, inventory or lead time is HIGH for investigation, explicitly not a confirmed shortage. Urgency is separate from ORDER/COVERED/REVIEW. Explanations state the coverage, lead time and reason; supplier summaries show their most urgent positive proposal.

## Manager review and approval

1. Review **Replenishment Recommendations**, including urgency.
2. Select a SKU under **Why this order?** and inspect the calculation and warnings.
3. Enter a nonnegative integer **Manager order quantity**. The algorithm's `recommended_order_qty` stays read-only; edits are stored as `manager_order_qty`.
4. Click **Approve reviewed quantity** to approve that SKU's demo decision and record its quantity and UTC timestamp. Zero-quantity decisions are valid.

The review table shows original and edited quantities, whether they differ, Pending/Approved state and approval time. A per-SKU session log retains edits, approvals and calculation changes. Editing an approved quantity resets it to Pending. Changed calculation inputs also invalidate approval: manual overrides survive, while untouched quantities follow the updated recommendation.

Streamlit session state preserves decisions across ordinary reruns and SKU switches. A new browser session, lost session or server restart may discard them. There is no authentication or durable approval database. **Approval never sends an order or contacts a supplier.**

## Supplier grouping and export

Brand/search filters and 25-SKU pagination prevent thousands of rows being rendered at once. Metrics, charts, proposals and exports are explicitly scoped to the current page. Workbook loading is cached by filename/size/modification time; page forecasts are cached by that fingerprint, SKU selection and planning lead time. Normal reruns do not reread Excel.

Supplier proposals group positive manager-reviewed quantities, including Pending ones, and show original quantities, urgency, data-review status and approval state. Totals use reviewed quantity times latest unit price. Zero-quantity decisions remain visible in the review table. Summary metrics and charts above this section continue to show calculated quantities, not overrides.

Use the download icon in a table's toolbar to export displayed columns as CSV. The review table exports original/edited quantities and decision states; supplier tables export proposal lines/totals. The explicit **Export reviewed recommendations (current page)** button includes full calculated adjustments, explanations, order constraints, original and reviewed quantities and approval state. A separate button exports the complete selected-SKU document audit. Manager quantities violating supplied constraints show a warning; the original compliant recommendation is preserved. There is no separate Excel generator or supplier dispatch integration.

## Assumptions and limitations

- Replenishment uses 30-day months; lost-demand reconstruction uses actual calendar days.
- The horizon is supplier lead time, without an additional periodic-review interval.
- Transit is assumed usable within that horizon; supplied expected arrival dates are retained in the inbound audit but are not time-phased by the replenishment formula. HIGH urgency flags reliance on timely transit.
- Synthetic calculations use the latest historical month; partner calculations use the supplied September 2026 snapshot origin, not today's date. Past evaluations must supply only history available at that forecast origin.
- Monthly aggregates cannot recover daily patterns, customer concentration or promotions without additional data. Historical outage durations must be supplied explicitly; zero inventory snapshots support only flagged comparable-period estimates.
- Without enough seasonal history, a seasonal ramp cannot always be distinguished from permanent growth.
- Safety stock assumes independent daily demand and is not a guaranteed service level. Constant observed sales yield zero estimated safety stock; fallbacks are heuristics, not guaranteed upper bounds.
- One common currency is assumed. Supplied shipment minimums/order multiples are modeled; taxes, shipping, capacity limits and cancellation of inbound orders are not.
- Customer-level anomaly analysis and anonymization of future customer imports are not implemented. Neither the synthetic schema nor the supplied partner document exports contains customer identifiers. Do not add raw customer PII to this dashboard.
- Approvals exist only in the current session and are not legal purchase-order authorization.

Further details: [forecasting methods](docs/demand_forecasting.md) and [replenishment assumptions](docs/replenishment.md).

## Automatic product-name translation

New product names are translated on demand with Google Cloud Translation Basic (v2). Russian and Kazakh (`kk`) are supported. The translation API key is read from the `TRANSLATION_API_KEY` environment variable; if it is missing or a request fails, the dashboard displays the original name. No extra Python package is needed.

1. In a billing-enabled Google Cloud project, enable the Cloud Translation API and create an API key restricted to that API.
2. Set the key in the shell where Streamlit will run. PowerShell example:

   ```powershell
   $env:TRANSLATION_API_KEY = "YOUR_GOOGLE_CLOUD_TRANSLATION_API_KEY"
   .venv\Scripts\python.exe -m streamlit run app.py
   ```

Translation results are cached in the operating system's user cache directory (`%LOCALAPPDATA%\Solai\product_translations.sqlite3` on Windows, or `$XDG_CACHE_HOME/solai/product_translations.sqlite3` / `~/.cache/solai/product_translations.sqlite3` on Linux/macOS). The cache key is the original product name plus target language. [Google Cloud language support](https://cloud.google.com/translate/docs/languages) · [Translation Basic v2 API](https://cloud.google.com/translate/docs/reference/rest/v2/translate).
