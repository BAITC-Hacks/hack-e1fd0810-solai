"""Human-readable explanations generated only from calculated SKU values."""

import pandas as pd

from src.forecasting import explain_forecast


def explain_replenishment(row: pd.Series) -> str:
    """Explain formulas, fallback, adjustments and review conditions."""
    parts = [f"Forecast demand is {row['forecast_demand']} units/month."]
    if "growth_factor" in row:
        parts.append(explain_forecast(row))
    if pd.notna(row["recommended_order_qty"]):
        parts.append(
            f"Daily demand is forecast / 30 = {row['daily_demand']:.2f} units/day. "
            f"Supplier lead time is {row['lead_time_days']} days, requiring "
            f"{row['daily_demand']:.2f} × {row['lead_time_days']} = "
            f"{row['lead_time_demand']:.2f} units during lead time."
        )
        parts.append(
            f"Safety stock is {row['service_level_factor']:.2f} × "
            f"{row['monthly_demand_std']:.2f} × sqrt({row['lead_time_days']} / 30) "
            f"= {row['safety_stock']:.2f} units. "
            f"The monthly variability estimate uses {row['history_observations']} "
            "non-anomalous, non-stockout observations; greater variability increases safety stock."
        )
        parts.append(
            f"Current stock is {row['current_stock']} units and {row['in_transit']} "
            f"units are in transit, giving an inventory position of {row['inventory_position']:.2f}. "
            f"Target stock is {row['lead_time_demand']:.2f} + {row['safety_stock']:.2f} "
            f"= {row['target_stock']:.2f}. "
            f"Raw need is {row['target_stock']:.2f} − {row['inventory_position']:.2f} "
            f"= {row['raw_order_qty']:.2f}. "
            f"The recommended replenishment quantity is {int(row['recommended_order_qty'])} units "
            "(raw need rounded up, with a minimum of zero). Calculations use unrounded values."
        )
        if row["recommended_order_qty"] == 0:
            parts.append("No order is recommended because current stock plus in-transit inventory meets or exceeds target stock.")
    else:
        parts.append("A reliable order quantity cannot be calculated from the supplied inputs; no quantity is proposed.")
    if row["safety_stock_method"] == "fallback_100pct_monthly":
        parts.append(
            "Insufficient-history fallback: fewer than three clean monthly observations are available. "
            "Monthly variability is set to the larger of forecast demand and the clean historical mean "
            "(a conservative 100% variability assumption). Manager review is required."
        )
    parts.append(
        f"{int(row['anomalies_detected'])} sales anomaly/anomalies were detected by Phase 1; "
        f"{int(row['anomalies_excluded'])} flagged observation(s) in the variability window "
        "were excluded from safety-stock estimation and retained for audit."
    )
    if row["seasonal_factor"] != 1:
        parts.append(f"Phase 1 already applied a seasonal factor of {row['seasonal_factor']:.4f} to demand; it is not applied again here.")
    if row.get("stockout_observations_excluded", 0):
        parts.append(f"{int(row['stockout_observations_excluded'])} stockout period(s) were excluded from safety-stock variability; imputed demand is not treated as observed variability.")
    if row["status"] == "REVIEW":
        parts.append(f"REVIEW required: {row['review_reasons'].replace(';', ', ').replace('_', ' ')}. Any calculated quantity is provisional.")
    parts.append("This is a proposal for manager review only. No supplier order is sent.")
    return " ".join(parts)
