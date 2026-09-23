"""Loading and basic validation for replenishment demo data."""

from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = {
    "date",
    "sku",
    "product_name",
    "category",
    "supplier",
    "sales",
    "current_stock",
    "in_transit",
    "lead_time_days",
    "unit_price",
}


def load_sample_data(path: str | Path) -> pd.DataFrame:
    """Load the sample CSV and ensure it has the fields the dashboard expects."""
    data = pd.read_csv(path, parse_dates=["date"])
    missing = REQUIRED_COLUMNS.difference(data.columns)
    if missing:
        raise ValueError(f"Dataset is missing required columns: {', '.join(sorted(missing))}")
    if data.empty:
        raise ValueError("Dataset contains no rows")
    return data.sort_values(["date", "sku"]).reset_index(drop=True)
