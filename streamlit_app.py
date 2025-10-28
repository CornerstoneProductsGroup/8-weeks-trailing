
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
    # Combine header rows up to header_row_idx into a single line of names
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
    # build new frame
    new_df = df.iloc[header_row_idx + 1 : ].copy()
    new_df.columns = final_cols
    return new_df

def guess_week_columns(columns):
    import re as _re
    pat = _re.compile(r"\b\d{1,2}-\d{1,2}\s*/\s*\d{1,2}-\d{1,2}\b")  # e.g., '1-1 / 1-3'
    weekish = [c for c in columns if pat.search(str(c))]
    # also include columns that contain 'Unit' or 'Sale' if they accompany weeks
    if weekish:
        extra = [c for c in columns if ("Unit" in str(c) or "Sales" in str(c)) and c not in weekish]
        return weekish + extra
    return []

def coerce_numeric(series):
    return pd.to_numeric(series, errors="coerce")

st.title("📊 8-Week Sales Explorer")

left, right = st.columns([2, 1])

with left:
    st.markdown("Upload an Excel file (your provided workbook is preloaded when running locally). Choose a sheet, define the header row, tidy the data, and explore KPIs and charts.")

with right:
    st.markdown("**Quick Start**")
    st.markdown("1) Pick a sheet → 2) Set header row → 3) Select ID vs Value columns → 4) Melt → 5) Explore.")

uploaded = st.file_uploader("Upload an Excel file", type=["xlsx","xls"])

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

st.write("Preview (first 12 rows):")
st.dataframe(raw_df.head(12))

header_row_idx = st.number_input("Header row index (0-based)", min_value=0, max_value=max(0, len(raw_df)-1), value=2, step=1)

df = make_headers(raw_df, header_row_idx)
st.write("Tidy preview after setting header row:")
st.dataframe(df.head(20))

# Column role selection
st.subheader("Select columns")
all_cols = list(df.columns)
# Guess week-like columns to preselect
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
st.dataframe(long_df.head(30))

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
        # text search filter
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
kpi_cols = st.multiselect("Pick numeric column(s) to summarize", value_cols if melt is False else ["Value"], default=(["Value"] if melt and "Value" in filtered.columns else []))
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
agg_target = st.selectbox("Aggregate column", kpi_cols if kpi_cols else (value_cols if value_cols else all_cols))
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
    st.dataframe(grouped.head(200))

# Chart
st.subheader("Chart")
chart_df = grouped if grouped is not None else filtered
x_col = st.selectbox("X axis", chart_df.columns if chart_df is not None else [])
y_col = st.selectbox("Y axis (numeric)", [c for c in chart_df.columns if c != x_col] if chart_df is not None else [])
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
    csv = to_export.to_csv(index=False).encode("utf-8")
    st.download_button("Download current table as CSV", csv, file_name="sales_export.csv", mime="text/csv")

st.caption("Tip: Save your chosen header row & selections by exporting the grouped result, then reload later.")
