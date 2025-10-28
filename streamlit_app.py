
import os
import io
import re
import sys
from datetime import datetime

import pandas as pd
import numpy as np
import streamlit as st
import matplotlib.pyplot as plt

st.set_page_config(page_title="8-Week Sales Explorer", layout="wide")

CURRENCY_HINT_WORDS = ["$", "sales", "revenue", "cost", "gm$", "gm $", "gross margin", "price", "amount", "dollars"]

@st.cache_data(show_spinner=False)
def load_excel(file_or_path):
    try:
        xls = pd.ExcelFile(file_or_path)
        sheets = {}
        for s in xls.sheet_names:
            try:
                df = pd.read_excel(xls, sheet_name=s, header=None)
            except Exception:
                df = pd.read_excel(file_or_path, sheet_name=s, header=None)
            sheets[s] = df
        return sheets
    except Exception as e:
        st.error(f"Failed to read Excel: {e}")
        return {}

def make_headers(df, header_row_idx):
    """Combine header rows up to header_row_idx into one header row, keeping column order = 'sheet-like'."""
    header_rows = df.iloc[: header_row_idx + 1].fillna("")
    tuples = list(zip(*[header_rows.iloc[i].astype(str).tolist() for i in range(header_rows.shape[0])]))
    # join non-empty parts with " | "
    cols = []
    for t in tuples:
        parts = [p.strip() for p in t if str(p).strip() not in ("", "nan", "NaN", "None")]
        cols.append(" | ".join(parts) if parts else "")
    # ensure uniqueness
    seen = {}
    final_cols = []
    for c in cols:
        if c == "":
            c = "Unnamed"
        if c in seen:
            seen[c] += 1
            final_cols.append(f"{c}.{seen[c]}")
        else:
            seen[c] = 0
            final_cols.append(c)
    new_df = df.iloc[header_row_idx + 1 : ].copy()
    new_df.columns = final_cols
    return new_df

def detect_currency_columns(df):
    """Heuristic: numeric columns whose header suggests dollars (contains $ or common money terms)."""
    currency_cols = []
    for c in df.columns:
        name = str(c).lower()
        if any(h in name for h in CURRENCY_HINT_WORDS):
            # numeric-ish?
            ser = pd.to_numeric(df[c], errors="coerce")
            if ser.notna().any():
                currency_cols.append(c)
    return currency_cols

def make_number_config(df, currency_cols, decimals=2):
    """Build Streamlit column_config for currency and other numeric columns."""
    try:
        from streamlit import column_config as cc
    except Exception:
        return {}

    config = {}
    # Currency format
    cur_fmt = f"$%,.{decimals}f"
    for c in currency_cols:
        config[c] = st.column_config.NumberColumn(format=cur_fmt)

    # Optionally, lightly format other pure numeric columns
    for c in df.columns:
        if c in currency_cols:
            continue
        ser = pd.to_numeric(df[c], errors="coerce")
        if ser.notna().any():
            config.setdefault(c, st.column_config.NumberColumn(format="%,.0f"))
    return config

def coerce_numeric(series):
    return pd.to_numeric(series, errors="coerce")

st.title("📊 8-Week Sales Explorer")

# ---------- Weekly update helper (local runs) ----------
with st.expander("Weekly update: replace the base workbook (local runs)", expanded=False):
    st.caption("If you run this app on your computer, you can replace the local copy stored at `data/8_weeks_trailing_2025.xlsx`. On Streamlit Cloud, just upload the newest file below each week.")
    new_file = st.file_uploader("Upload a new weekly workbook to replace the local copy", type=["xlsx","xls"], key="weekly_updater")
    if new_file is not None:
        os.makedirs("data", exist_ok=True)
        path = "data/8_weeks_trailing_2025.xlsx"
        with open(path, "wb") as f:
            f.write(new_file.getbuffer())
        st.success("Replaced local base workbook. Reload the page to use it by default.")

left, right = st.columns([2, 1])

with left:
    st.markdown("**Step 1. Load your workbook**")
    st.markdown("Upload this week’s Excel file, or if you're running locally and keep your file in `data/8_weeks_trailing_2025.xlsx`, the app will load it automatically.")

with right:
    st.markdown("**Tip**: The **Sheet View** tab below mirrors your Excel layout (wide), while **Explorer** gives flexible analysis, grouping, and charts.")

uploaded = st.file_uploader("Upload Excel", type=["xlsx","xls"], key="main_uploader")

# Try local default if present (local dev), otherwise require upload (Streamlit Cloud)
default_path = "data/8_weeks_trailing_2025.xlsx"
sheets = {}
if uploaded is not None:
    sheets = load_excel(uploaded)
elif os.path.exists(default_path):
    sheets = load_excel(default_path)
else:
    st.info("Upload your Excel file to begin (no local default found).")
    st.stop()

sheet_name = st.selectbox("Sheet", list(sheets.keys()))
raw_df = sheets[sheet_name]

# Header setup
st.subheader("Step 2. Header row")
st.write("Preview (first 12 rows):")
st.dataframe(raw_df.head(12))

header_row_idx = st.number_input("Header row index (0-based)", min_value=0, max_value=max(0, len(raw_df)-1), value=2, step=1)

df = make_headers(raw_df, header_row_idx)

# Detect currency columns and allow override
auto_currency_cols = detect_currency_columns(df)
st.subheader("Step 3. Formatting")
st.caption("Columns detected as $ can be adjusted below. These will render with dollar formatting throughout the app.")
forced_currency_cols = st.multiselect("Treat these columns as $", options=list(df.columns), default=auto_currency_cols)
currency_decimals = st.slider("Currency decimals", 0, 4, 2)

# Build column_config for sheet-like rendering
col_cfg = make_number_config(df, forced_currency_cols, decimals=currency_decimals)

# ---------------- TABS ----------------
tab_sheet, tab_explorer = st.tabs(["🧾 Sheet View (like Excel)", "🧪 Explorer (group, chart)"])

with tab_sheet:
    st.markdown("This view preserves your sheet's wide layout and column order.")
    st.dataframe(df, use_container_width=True, hide_index=True, column_config=col_cfg)

    # Option to freeze first N columns isn't available in Streamlit yet; keep leftmost columns as-is.
    st.download_button("Download Sheet View as CSV", df.to_csv(index=False).encode("utf-8"), file_name="sheet_view.csv", mime="text/csv")

with tab_explorer:
    st.write("Tidy preview for analysis:")
    st.dataframe(df.head(20), use_container_width=True, hide_index=True, column_config=col_cfg)

    # Column role selection
    st.subheader("Select columns")
    all_cols = list(df.columns)

    # Guess week-like columns to preselect
    def guess_week_columns(columns):
        pat = re.compile(r"\b\d{1,2}-\d{1,2}\s*/\s*\d{1,2}-\d{1,2}\b")  # e.g., '1-1 / 1-3'
        weekish = [c for c in columns if pat.search(str(c))]
        if weekish:
            extra = [c for c in columns if ("Unit" in str(c) or "Sales" in str(c) or "$" in str(c)) and c not in weekish]
            return weekish + extra
        return []

    pre_week_cols = guess_week_columns(all_cols)
    id_cols = st.multiselect("ID / Dimension columns (e.g., Retailer, Product, Category)", [c for c in all_cols if c not in pre_week_cols])
    value_cols = st.multiselect("Value columns (numeric metrics to analyze)", all_cols, default=pre_week_cols if pre_week_cols else None)

    # Try to coerce numeric for selected value columns
    work = df.copy()
    for c in value_cols:
        work[c] = coerce_numeric(work[c])

    st.divider()
    st.subheader("Optional: melt value columns into long format")
    melt = st.checkbox("Melt to long format", value=True, help="Turn multiple value columns into a single 'Metric' + 'Value' pair.")

    if melt and value_cols:
        long_df = work.melt(id_vars=id_cols, value_vars=value_cols, var_name="Metric", value_name="Value")
    else:
        long_df = work.copy()

    st.write("Working data sample:")
    st.dataframe(long_df.head(30), use_container_width=True, hide_index=True, column_config=col_cfg)

    # Filters
    st.subheader("Filters")
    filters = {}
    for c in id_cols:
        uniques = sorted([x for x in pd.unique(long_df[c]) if str(x) != "nan"])
        if len(uniques) <= 200:
            sel = st.multiselect(f"{c}", uniques, default=uniques[: min(10, len(uniques))])
            if sel:
                filters[c] = sel
        else:
            query = st.text_input(f"Contains filter for {c}")
            if query:
                filters[c] = query

    filtered = long_df.copy()
    for c, v in filters.items():
        if isinstance(v, list):
            filtered = filtered[filtered[c].isin(v)]
        else:
            filtered = filtered[filtered[c].astype(str).str.contains(str(v), case=False, na=False)]

    st.divider()

    # KPIs
    st.subheader("KPIs")
    default_kpi_cols = ["Value"] if (melt and "Value" in filtered.columns) else [c for c in forced_currency_cols if c in filtered.columns]
    kpi_cols = st.multiselect("Pick numeric column(s) to summarize", [c for c in filtered.columns if c in forced_currency_cols or pd.api.types.is_numeric_dtype(pd.to_numeric(filtered[c], errors='coerce'))], default=default_kpi_cols)
    if kpi_cols:
        kpis = {}
        for c in kpi_cols:
            series = pd.to_numeric(filtered[c], errors="coerce")
            kpis[c] = {
                "sum": float(np.nansum(series)),
                "avg": float(np.nanmean(series)) if series.notna().any() else float("nan"),
                "min": float(np.nanmin(series)) if series.notna().any() else float("nan"),
                "max": float(np.nanmax(series)) if series.notna().any() else float("nan"),
            }
        st.json(kpis)

    # Grouping
    st.subheader("Group & Aggregate")
    group_cols = st.multiselect("Group by", id_cols + (["Metric"] if melt else []))
    agg_target_pool = kpi_cols if kpi_cols else (value_cols if value_cols else all_cols)
    agg_target = st.selectbox("Aggregate column", agg_target_pool)
    agg_func = st.selectbox("Aggregation", ["sum", "mean", "min", "max", "count"])

    grouped = None
    if group_cols and agg_target:
        temp = filtered.copy()
        temp[agg_target] = pd.to_numeric(temp[agg_target], errors="coerce")
        if agg_func == "sum":
            grouped = temp.groupby(group_cols, dropna=False)[agg_target].sum().reset_index()
        elif agg_func == "mean":
            grouped = temp.groupby(group_cols, dropna=False)[agg_target].mean().reset_index()
        elif agg_func == "min":
            grouped = temp.groupby(group_cols, dropna=False)[agg_target].min().reset_index()
        elif agg_func == "max":
            grouped = temp.groupby(group_cols, dropna=False)[agg_target].max().reset_index()
        elif agg_func == "count":
            grouped = temp.groupby(group_cols, dropna=False)[agg_target].count().reset_index()
        st.write("Grouped result")
        st.dataframe(grouped.head(200), use_container_width=True, hide_index=True, column_config=col_cfg)

    # Chart
    st.subheader("Chart")
    chart_df = grouped if grouped is not None else filtered
    x_col = st.selectbox("X axis", chart_df.columns if chart_df is not None else [])
    y_candidates = [c for c in chart_df.columns if c != x_col]
    y_col = st.selectbox("Y axis (numeric)", y_candidates if chart_df is not None else [])
    series_col = st.selectbox("Series (optional)", ["(none)"] + [c for c in chart_df.columns if c not in [x_col, y_col]] if chart_df is not None else [])

    if chart_df is not None and x_col and y_col:
        try:
            fig, ax = plt.subplots(figsize=(10,4))
            plot_df = chart_df.copy()
            plot_df[y_col] = pd.to_numeric(plot_df[y_col], errors="coerce")
            plot_df = plot_df.dropna(subset=[y_col])
            if series_col and series_col != "(none)":
                for key, sub in plot_df.groupby(series_col):
                    ax.plot(sub[x_col].astype(str), sub[y_col], marker="o", label=str(key))
                ax.legend(loc="best")
            else:
                ax.plot(plot_df[x_col].astype(str), plot_df[y_col], marker="o")
            ax.set_xlabel(x_col)
            ax.set_ylabel(y_col)
            ax.set_title("Chart")
            ax.grid(True, linestyle="--", alpha=0.4)
            st.pyplot(fig)
        except Exception as e:
            st.warning(f"Could not render chart: {e}")

    # Export
    st.subheader("Export")
    to_export = grouped if grouped is not None else filtered
    if to_export is not None and len(to_export):
        st.download_button("Download current table as CSV", to_export.to_csv(index=False).encode("utf-8"), file_name="explorer_export.csv", mime="text/csv")

st.caption("Sheet View mirrors your Excel layout; currency columns render with $ formatting. Upload a new workbook each week and select the right header row—your formatting rules apply automatically.")
