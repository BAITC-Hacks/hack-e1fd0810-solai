"""Locate UI evidence by schema rather than a presentation-specific table index."""


def forecast_table(app):
    return next(item.value for item in app.dataframe
                if {"raw_sales_baseline", "stockout_adjustment", "estimated_lost_demand", "growth_factor"}.issubset(item.value.columns))
