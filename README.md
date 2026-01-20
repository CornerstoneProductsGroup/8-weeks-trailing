# Weekly Retailer Report (Online)

This Streamlit app replaces the Excel workflow with an online, persistent report.

## Key features
- Retailer selector (from Vendor-SKU Map)
- Multi-week view: weeks appear as column headers (e.g., `1-5 / 1-9`)
- Retailer + SKU stay on the left
- Unit Price comes from the Vendor-SKU Map (any column containing 'price')
- Total $ is calculated: Units × Unit Price (read-only)
- Sales is a single column on the far right (manual, optional)
- Upload weekly export files to auto-fill Units for a chosen week

## Setup / Run
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Mapping
The repo includes `Vendor-SKU Map.xlsx`. On first run, it loads automatically into the database.
Upload a new map in the sidebar only if you want to replace it.

## Notes on editing
- You pick an **Edit Week**.
- Only that week’s column is editable (units override).
- Sales + Notes are always editable (and apply to the Edit Week).

## Data storage
SQLite database file: `app.db`
