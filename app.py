
import io, re, json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

from store import (
    load_catalog, save_catalog,
    load_facts, save_facts,
    load_weeks_order, save_weeks_order, ensure_week_in_order,
)

st.set_page_config(page_title="Trailing Report (Exact Layout)", layout="wide")
st.title("📑 Trailing Report — Matches Your Master Layout")
st.caption("Retailer tabs • Weekly uploads by retailer sheets • Period totals per SKU")

def _parse_week_label_from_filename(name: str) -> str | None:
    m = re.search(r"(\d{1,2}[-/]\d{1,2}).*?(\d{1,2}[-/]\d{1,2})", name.replace("\\", "/"))
    if not m: return None
    a, b = m.group(1), m.group(2)
    a = a.replace("/", "-"); b = b.replace("/", "-")
    return f"{a} / {b}"

def _normalize_weekly_sheet(df: pd.DataFrame) -> pd.DataFrame:
    mapping = {c.lower().strip().replace(" ", ""): c for c in df.columns}
    col_sku = next((mapping[k] for k in mapping if k in ("sku","#sku","sku#","sku_number","skunumber")), None)
    col_units = next((mapping[k] for k in mapping if k in ("units","qty","quantity","unitssold","units_sold")), None)
    out = pd.DataFrame()
    if col_sku is None or col_units is None:
        return out
    out["SKU"] = df[col_sku].astype(str).str.strip()
    out["Units"] = pd.to_numeric(df[col_units], errors="coerce").fillna(0.0)
    return out

def _period_columns(weeks_order: list[str], start_label: str, end_label: str) -> list[str]:
    if not weeks_order: return []
    try:
        i0 = weeks_order.index(start_label); i1 = weeks_order.index(end_label)
        if i0 > i1: i0, i1 = i1, i0
        return weeks_order[i0:i1+1]
    except ValueError:
        return weeks_order[-8:]

catalog = load_catalog()
facts = load_facts()
weeks_order = load_weeks_order()

with st.sidebar:
    st.header("Initialize from Master")
    st.caption("Upload your 8 weeks trailing master to seed retailers/SKUs and weeks.")
    master_up = st.file_uploader("Master workbook (.xlsx)", type=["xlsx"], key="master")
    if st.button("📥 Initialize from Master", use_container_width=True, disabled=(master_up is None)):
        if master_up is None:
            st.error("Upload the master workbook first.")
        else:
            xls = pd.ExcelFile(master_up)
            all_weeks = []
            cat_rows = []
            for sheet in xls.sheet_names:
                df = pd.read_excel(xls, sheet_name=sheet, header=None)
                header_idx = None
                for i in range(min(12, len(df))):
                    row = df.iloc[i].astype(str).str.strip()
                    week_labels = [v for v in row if re.match(r"^\d{1,2}\s*-\s*\d{1,2}\s*/\s*\d{1,2}\s*-\s*\d{1,2}$", v)]
                    if week_labels:
                        header_idx = i
                        for w in week_labels:
                            if w not in all_weeks: all_weeks.append(w)
                        break
                if header_idx is None: continue
                header = df.iloc[header_idx]
                sku_col = None
                for j, v in enumerate(header):
                    if isinstance(v, str) and v.strip().lower() == "unit cost":
                        sku_col = j + 1; break
                if sku_col is None:
                    for j, v in enumerate(header):
                        if isinstance(v, str) and v.strip().lower() == "sku":
                            sku_col = j; break
                if sku_col is None: continue
                retailer_name = str(df.iloc[header_idx - 1, 0]).strip() if header_idx - 1 >= 0 else sheet
                for _, row in df.iloc[header_idx + 1 :].iterrows():
                    val = row.iloc[sku_col] if sku_col < len(row) else None
                    if val is None or str(val).strip() in ("", "nan"): continue
                    cat_rows.append({"RetailerGroup": retailer_name, "SKU": str(val).strip()})
            if cat_rows:
                cat_df = pd.DataFrame(cat_rows).drop_duplicates()
                save_catalog(cat_df); st.success(f"Catalog initialized with {len(cat_df)} (Retailer, SKU) pairs.")
            else:
                st.warning("No SKUs detected in master.")
            if all_weeks:
                save_weeks_order(all_weeks); st.success(f"Detected {len(all_weeks)} week columns.")
            else:
                st.info("No week columns detected.")

    st.markdown("---")
    st.header("Weekly Upload (Each Sheet = Retailer)")
    weekly_up = st.file_uploader("Weekly workbook (.xlsx)", type=["xlsx"], key="weekly")
    suggested = _parse_week_label_from_filename(weekly_up.name) if weekly_up else None
    week_label = st.text_input("Week label (e.g., '8-25 / 8-29')", value=(suggested or (weeks_order[-1] if weeks_order else "8-24 / 8-30")))
    auto_add = st.checkbox("Auto-add unknown SKUs to catalog", value=True)
    if st.button("➕ Append Weekly Workbook", use_container_width=True, disabled=(weekly_up is None)):
        if weekly_up is None:
            st.error("Upload the weekly workbook first.")
        else:
            xls = pd.ExcelFile(weekly_up)
            rows = []
            for sheet in xls.sheet_names:
                df = pd.read_excel(xls, sheet_name=sheet, header=0)
                norm = _normalize_weekly_sheet(df)
                if norm.empty: continue
                norm["RetailerGroup"] = sheet
                norm["WeekLabel"] = week_label
                rows.append(norm[["WeekLabel","RetailerGroup","SKU","Units"]])
            if not rows:
                st.error("No valid sheets found (need columns like SKU and Units).")
            else:
                add = pd.concat(rows, ignore_index=True)
                if auto_add:
                    cat = load_catalog()
                    to_add = add[["RetailerGroup","SKU"]].drop_duplicates()
                    save_catalog(pd.concat([cat, to_add], ignore_index=True).drop_duplicates())
                cur = load_facts()
                save_facts(pd.concat([cur, add], ignore_index=True))
                ensure_week_in_order(week_label)
                st.success(f"Appended {len(add)} rows for '{week_label}'.")

st.markdown("---")
st.subheader("Reports")

catalog = load_catalog()
facts = load_facts()
weeks_order = load_weeks_order()

if facts.empty and catalog.empty:
    st.info("Initialize from Master or append a weekly workbook to begin.")
else:
    retailers = sorted(set(catalog["RetailerGroup"].dropna().astype(str).unique().tolist()) |
                       set(facts["RetailerGroup"].dropna().astype(str).unique().tolist()))
    if not weeks_order:
        weeks_order = sorted(facts["WeekLabel"].dropna().astype(str).unique().tolist())

    if not retailers:
        st.info("No retailers detected yet.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            start_label = st.selectbox("Start week", options=weeks_order, index=0)
        with c2:
            end_label = st.selectbox("End week", options=weeks_order, index=len(weeks_order)-1)

        def _period_columns(order, s, e):
            try:
                i0 = order.index(s); i1 = order.index(e)
                if i0 > i1: i0, i1 = i1, i0
                return order[i0:i1+1]
            except ValueError:
                return order[-8:]
        period_weeks = _period_columns(weeks_order, start_label, end_label)
        st.write(f"Selected period: **{', '.join(period_weeks)}**")

        pivot = facts.pivot_table(index=["RetailerGroup","SKU"], columns="WeekLabel", values="Units",
                                  aggfunc="sum", fill_value=0.0)
        for w in period_weeks:
            if w not in pivot.columns: pivot[w] = 0.0
        pivot = pivot[period_weeks]
        pivot["Period Units Total"] = pivot.sum(axis=1)

        tabs = st.tabs(retailers)
        for tab, r in zip(tabs, retailers):
            with tab:
                st.markdown(f"### {r}")
                idx = pivot.index.get_level_values(0) == r
                tbl = pivot[idx].copy()
                st.dataframe(tbl, use_container_width=True)

                total_row = pd.DataFrame(tbl.sum(axis=0)).T; total_row.index = ["__TOTAL__"]
                st.markdown("**Retailer totals (Units)**")
                st.dataframe(total_row, use_container_width=True)

        excel_buf = io.BytesIO()
        with pd.ExcelWriter(excel_buf, engine="openpyxl") as writer:
            for r in retailers:
                idx = pivot.index.get_level_values(0) == r
                tbl = pivot[idx].copy()
                tbl.to_excel(writer, sheet_name=r[:31])
        excel_buf.seek(0)
        st.download_button("⬇️ Download Period Report (Excel)", data=excel_buf.read(),
                           file_name="trailing_period_report.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True)
