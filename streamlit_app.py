
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

# ---------- Paths ----------
BUILT_IN_FACT = "data/built_in_sales.csv"
VENDOR_MAP_PATH = "data/vendor_map.csv"

# ---------- Helpers ----------
def load_built_in():
    if os.path.exists(BUILT_IN_FACT):
        try:
            return pd.read_csv(BUILT_IN_FACT)
        except Exception as e:
            st.error(f"Failed to load built-in data: {e}")
    return pd.DataFrame(columns=["sheet","sku","unit_cost","units_sold_total"])

def load_vendor_map():
    if os.path.exists(VENDOR_MAP_PATH):
        try:
            return pd.read_csv(VENDOR_MAP_PATH).astype({"sku": str, "vendor": str})
        except Exception as e:
            st.warning(f"Couldn't read vendor map: {e}")
    return pd.DataFrame({"sku": pd.Series(dtype=str), "vendor": pd.Series(dtype=str)})

def save_vendor_map(df: pd.DataFrame):
    os.makedirs(os.path.dirname(VENDOR_MAP_PATH), exist_ok=True)
    df.to_csv(VENDOR_MAP_PATH, index=False)

def money_fmt(x, decimals=2):
    try:
        val = float(x)
        return f"${val:,.{decimals}f}"
    except:
        return x

# ---------- Load datasets ----------
fact = load_built_in()  # columns: sheet, sku, unit_cost, units_sold_total
vendor_map = load_vendor_map()

# Merge vendor names
fact["sku"] = fact["sku"].astype(str)
vendor_map["sku"] = vendor_map["sku"].astype(str)
df = fact.merge(vendor_map, on="sku", how="left")

# Derived fields
df["revenue_est"] = pd.to_numeric(df["unit_cost"], errors="coerce") * pd.to_numeric(df["units_sold_total"], errors="coerce")

st.title("📦 Sales by Vendor / SKU (Built-In)")

# ================== Sidebar: Vendor Map Manager ==================
with st.sidebar:
    st.header("Vendor/SKU Mapping")
    st.caption("Upload or edit a simple two-column file: `sku,vendor`. This powers vendor-level views.")
    uploaded_map = st.file_uploader("Upload vendor_map.csv", type=["csv"], key="vm_upload")
    if uploaded_map is not None:
        try:
            new_map = pd.read_csv(uploaded_map).astype({"sku": str, "vendor": str})
            save_vendor_map(new_map)
            vendor_map = new_map
            df = fact.merge(vendor_map, on="sku", how="left")
            df["revenue_est"] = pd.to_numeric(df["unit_cost"], errors="coerce") * pd.to_numeric(df["units_sold_total"], errors="coerce")
            st.success("Vendor map updated and applied.")
        except Exception as e:
            st.error(f"Could not read that CSV: {e}")

    st.caption("Or edit inline below (then click **Save**):")
    edit_map = st.data_editor(vendor_map, num_rows="dynamic", use_container_width=True, key="vm_editor")
    if st.button("Save Vendor Map"):
        try:
            save_vendor_map(edit_map)
            st.success("Saved vendor_map.csv")
        except Exception as e:
            st.error(f"Failed to save map: {e}")

    st.divider()
    st.caption("Download current vendor_map.csv")
    st.download_button("Download vendor_map.csv", (edit_map if isinstance(edit_map, pd.DataFrame) else vendor_map).to_csv(index=False).encode("utf-8"), file_name="vendor_map.csv", mime="text/csv")

# ================== Main Tabs ==================
tab_dash, tab_vendor, tab_sku, tab_sheet, tab_explorer = st.tabs([
    "📈 Dashboard",
    "🏷️ By Vendor",
    "🔢 By SKU",
    "🧾 Sheet View",
    "🧪 Explorer",
])

# ---------- Dashboard ----------
with tab_dash:
    st.subheader("Topline")
    total_units = pd.to_numeric(df["units_sold_total"], errors="coerce").sum()
    total_rev = pd.to_numeric(df["revenue_est"], errors="coerce").sum()
    col1, col2, col3 = st.columns(3)
    col1.metric("Total SKUs", f"{df['sku'].nunique():,}")
    col2.metric("Total Units Sold", f"{int(total_units):,}")
    col3.metric("Revenue (est.)", money_fmt(total_rev))

    st.divider()
    # Top vendors / SKUs
    st.subheader("Leaders")
    vendor_view = df.groupby("vendor", dropna=False)["units_sold_total"].sum().reset_index().sort_values("units_sold_total", ascending=False).head(20)
    vendor_view.rename(columns={"units_sold_total":"Units"}, inplace=True)
    sku_view = df.groupby("sku", dropna=False)["units_sold_total"].sum().reset_index().sort_values("units_sold_total", ascending=False).head(20)
    sku_view.rename(columns={"units_sold_total":"Units"}, inplace=True)

    c1, c2 = st.columns(2)
    c1.write("Top Vendors (by Units)")
    c1.dataframe(vendor_view, use_container_width=True, hide_index=True)
    c2.write("Top SKUs (by Units)")
    c2.dataframe(sku_view, use_container_width=True, hide_index=True)

# ---------- By Vendor ----------
with tab_vendor:
    st.subheader("Vendor Drilldown")
    vendors = ["(Unmapped)"] + sorted([v for v in df["vendor"].dropna().unique().tolist()])
    selected_vendor = st.selectbox("Vendor", vendors)
    if selected_vendor == "(Unmapped)":
        sub = df[df["vendor"].isna()]
    else:
        sub = df[df["vendor"] == selected_vendor]

    st.write(f"**Units Sold**: {int(pd.to_numeric(sub['units_sold_total'], errors='coerce').sum()):,}")
    st.write(f"**Revenue (est.)**: {money_fmt(pd.to_numeric(sub['revenue_est'], errors='coerce').sum())}")
    st.dataframe(sub[["sheet","sku","unit_cost","units_sold_total","revenue_est"]], use_container_width=True, hide_index=True, column_config={
        "unit_cost": st.column_config.NumberColumn(format="$%,.2f"),
        "units_sold_total": st.column_config.NumberColumn(format="%,.0f"),
        "revenue_est": st.column_config.NumberColumn(format="$%,.2f"),
    })

# ---------- By SKU ----------
with tab_sku:
    st.subheader("SKU Drilldown")
    all_skus = sorted(df["sku"].unique().tolist())
    sku_pick = st.selectbox("SKU", all_skus)
    ssub = df[df["sku"] == sku_pick]
    st.write(f"**Units Sold**: {int(pd.to_numeric(ssub['units_sold_total'], errors='coerce').sum()):,}")
    st.write(f"**Revenue (est.)**: {money_fmt(pd.to_numeric(ssub['revenue_est'], errors='coerce').sum())}")
    st.dataframe(ssub[["sheet","sku","unit_cost","units_sold_total","revenue_est"]], use_container_width=True, hide_index=True, column_config={
        "unit_cost": st.column_config.NumberColumn(format="$%,.2f"),
        "units_sold_total": st.column_config.NumberColumn(format="%,.0f"),
        "revenue_est": st.column_config.NumberColumn(format="$%,.2f"),
    })

# ---------- Sheet View (preserve layout) ----------
with tab_sheet:
    st.subheader("Sheet View (built-in data not shown here)")
    st.caption("For built-in data we aggregate totals. If you upload a workbook below, you can view the raw sheet layout.")
    uploaded = st.file_uploader("Optional: upload Excel to preview sheets", type=["xlsx","xls"], key="sheet_preview")
    if uploaded is not None:
        try:
            xls = pd.ExcelFile(uploaded)
            sname = st.selectbox("Sheet", xls.sheet_names, key="sheetname_preview")
            raw = pd.read_excel(xls, sheet_name=sname, header=None)
            st.dataframe(raw, use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(f"Failed to render sheet: {e}")

# ---------- Explorer (the flexible analysis UI) ----------
with tab_explorer:
    st.caption("Use the flexible explorer with a freshly uploaded workbook if you want ad hoc grouping and charts.")
    st.write("Upload an Excel file:")
    uploaded2 = st.file_uploader("Upload Excel for ad hoc analysis", type=["xlsx","xls"], key="explorer_upload")
    if uploaded2 is not None:
        from io import BytesIO

        @st.cache_data(show_spinner=False)
        def load_excel(file_or_path):
            xls = pd.ExcelFile(file_or_path)
            sheets = {}
            for s in xls.sheet_names:
                df = pd.read_excel(xls, sheet_name=s, header=None)
                sheets[s] = df
            return sheets

        def make_headers(df, header_row_idx):
            header_rows = df.iloc[: header_row_idx + 1].fillna("")
            tuples = list(zip(*[header_rows.iloc[i].astype(str).tolist() for i in range(header_rows.shape[0])]))
            cols = []
            for t in tuples:
                parts = [p.strip() for p in t if str(p).strip() not in ("", "nan", "NaN", "None")]
                cols.append(" | ".join(parts) if parts else "")
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

        sheets = load_excel(uploaded2)
        sname2 = st.selectbox("Sheet", list(sheets.keys()), key="exp_s")
        raw2 = sheets[sname2]
        st.write("Preview (first 12 rows):")
        st.dataframe(raw2.head(12), use_container_width=True, hide_index=True)

        header_row_idx = st.number_input("Header row index (0-based)", min_value=0, max_value=max(0, len(raw2)-1), value=2, step=1, key="hdr_idx2")
        df2 = make_headers(raw2, header_row_idx)
        st.dataframe(df2.head(30), use_container_width=True, hide_index=True)
        # very light aggregation helper
        num_cols = [c for c in df2.columns if pd.to_numeric(df2[c], errors="coerce").notna().any()]
        dim_cols = [c for c in df2.columns if c not in num_cols]
        group_cols = st.multiselect("Group by", dim_cols, max_selections=3)
        value_col = st.selectbox("Value", num_cols)
        agg = st.selectbox("Agg", ["sum","mean","min","max","count"])
        if group_cols and value_col:
            t = df2.copy()
            t[value_col] = pd.to_numeric(t[value_col], errors="coerce")
            if agg=="sum":
                g = t.groupby(group_cols, dropna=False)[value_col].sum().reset_index()
            elif agg=="mean":
                g = t.groupby(group_cols, dropna=False)[value_col].mean().reset_index()
            elif agg=="min":
                g = t.groupby(group_cols, dropna=False)[value_col].min().reset_index()
            elif agg=="max":
                g = t.groupby(group_cols, dropna=False)[value_col].max().reset_index()
            elif agg=="count":
                g = t.groupby(group_cols, dropna=False)[value_col].count().reset_index()
            st.dataframe(g, use_container_width=True, hide_index=True)
