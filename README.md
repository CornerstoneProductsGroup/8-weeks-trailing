
# 8‑Week Sales Explorer (Streamlit)

An interactive app to explore your "8 weeks trailing 2025" Excel workbook. The app lets you:
- Pick a sheet and define the correct header row (handles messy multi-row headers)
- Select ID (dimension) columns and numeric value columns
- Optionally "melt" multiple value columns into a long format for easier grouping
- Filter, group, aggregate, and chart results (matplotlib)
- Export the current table to CSV

## Quick Start

1. Ensure you have Python 3.9+ installed.
2. Create & activate a virtual environment (recommended).
3. Install dependencies:
   ```bash
   pip install streamlit pandas numpy matplotlib openpyxl
   ```
4. Run the app:
   ```bash
   streamlit run streamlit_app.py
   ```
5. The app ships with a copy of your workbook at `data/8_weeks_trailing_2025.xlsx` preloaded. You can also upload a newer workbook from within the UI.

## Notes
- The "Header row index" is crucial. Try 1, 2, or 3 if the first row(s) contain titles/month names.
- Use "Melt to long format" if you have many week or metric columns and want a single "Metric" + "Value" column.
- Charts use matplotlib and will automatically coerce numeric data where possible.

