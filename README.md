# Weekly Retailer Report (Online)

This Streamlit app replaces the Excel "8 weeks trailing" report with an online, persistent version.

## What it does
- Retailer selector (tabs/dropdown)
- Week selector (2026 only in v1): `1-1 / 1-2`, then Monday–Friday week ranges
- Upload your Vendor-SKU Map (columns: Retailer, SKU, Vendor)
- Upload weekly retailer export workbook (like your example)
- Auto-fills Units for that week
- You manually enter Sales + Notes (autosaved to SQLite)

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Data storage
- Uses SQLite database file `app.db` in the same folder
- Back it up by copying `app.db`

## Notes
- Import parsers are based on your provided weekly export layout:
  - Depot / Lowe's: SKU col E, Qty col F (no headers)
  - Depot SO: SKU col D, Qty col E
  - Amazon: SKU col C, Units Sold for Period col N
  - TSC: columns Vendor Style / Qty Ordered

Year selector and multi-year support can be added later.
