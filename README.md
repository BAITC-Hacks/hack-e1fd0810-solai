# Solai Procurement Replenishment MVP

A small Streamlit foundation for a logistics and procurement hackathon MVP. The intended system will help warehouse teams review supplier replenishment recommendations using sales history, available stock, inbound goods, product category, supplier and lead time. Recommendations are for human review only: the application will not send purchase orders to suppliers.

## Architecture

- `app.py` is the Streamlit dashboard entry point. It loads and displays the bundled sample dataset.
- `src/data_loader.py` owns sample data loading and basic input validation.
- `src/forecasting.py` will hold demand forecast logic that accounts for seasonality, sustainable growth and censored demand during stockouts.
- `src/anomaly_detection.py` will identify stockout periods and one-time sales spikes.
- `src/replenishment.py` will calculate draft SKU quantities and group recommendations by supplier.
- `src/explanations.py` will prepare the per-SKU reasoning shown to procurement managers.
- `data/sample_data.csv` contains synthetic monthly sales history and current inventory snapshots for the demo.
- `tests/` is reserved for automated checks as the algorithms are implemented.

The forecasting and replenishment modules are placeholders in this initial scaffold. The application currently displays source data only.

## Setup

Requires Python 3.10 or newer.

```bash
python -m venv .venv
```

Activate the environment:

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Run the Streamlit application

From the repository root, run:

```bash
streamlit run app.py
```

Streamlit will open the dashboard in your browser. It shows the sample sales history and inventory data; it does not create or send supplier orders.

## Automatic product-name translation

New product names are translated on demand with Google Cloud Translation Basic (v2). Russian and Kazakh (`kk`) are supported. The translation API key is read from the `TRANSLATION_API_KEY` environment variable; if it is missing or a request fails, the dashboard displays the original name. No extra Python package is needed.

1. In a billing-enabled Google Cloud project, enable the Cloud Translation API and create an API key restricted to that API.
2. Set the key in the shell where Streamlit will run. PowerShell example:

   ```powershell
   $env:TRANSLATION_API_KEY = "YOUR_GOOGLE_CLOUD_TRANSLATION_API_KEY"
   .venv\Scripts\python.exe -m streamlit run app.py
   ```

Translation results are cached in the operating system's user cache directory (`%LOCALAPPDATA%\Solai\product_translations.sqlite3` on Windows, or `$XDG_CACHE_HOME/solai/product_translations.sqlite3` / `~/.cache/solai/product_translations.sqlite3` on Linux/macOS). The cache key is the original product name plus target language. [Google Cloud language support](https://cloud.google.com/translate/docs/languages) · [Translation Basic v2 API](https://cloud.google.com/translate/docs/reference/rest/v2/translate).
