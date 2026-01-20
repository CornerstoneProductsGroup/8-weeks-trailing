import streamlit as st
import pandas as pd
import sqlite3
import json
from datetime import date, datetime, timedelta
from openpyxl import load_workbook
import os
import re

def parse_week_label_from_filename(filename: str):
    """
    Accepts filenames like:
      'APP 1-1 thru 1-2.xlsx'
      'APP 1-5 thru 1-9.xlsx'
    Returns (label, start_date, end_date) for 2026, or (None,None,None) if not matched.
    """
    base = os.path.basename(filename)
    m = re.search(r'APP\s+(\d{1,2})-(\d{1,2})\s+thru\s+(\d{1,2})-(\d{1,2})', base, re.IGNORECASE)
    if not m:
        return None, None, None
    m1, d1, m2, d2 = map(int, m.groups())
    try:
        start = date(2026, m1, d1)
        end = date(2026, m2, d2)
    except Exception:
        return None, None, None
    label = f"{m1}-{d1} / {m2}-{d2}"
    return label, start, end

def norm_name(s: str):
    if s is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())

def normalize_retailer_sheet_name(sheet_name: str):
    """
    Maps uploaded sheet names to your retailer names used in the mapping.
    Adjust here if your mapping uses different naming.
    """
    n = norm_name(sheet_name)
    if n in ("depot", "homedepot", "thehomedepot"):
        return "Depot"
    if n in ("lowes", "lowesinc"):
        return "Lowe's"
    if n == "amazon":
        return "Amazon"
    if n in ("tractorsupply", "tsc", "tractorsupplyco"):
        return "Tractor Supply"
    if n in ("depotso", "depotspecialorders", "specialorders"):
        return "Depot SO"
    return sheet_name  # fallback

def parse_app_aggregated_sheet(ws):
    """
    APP workbook format: SKU in col A, Units in col B (already aggregated).
    Skips title rows like 'Depot '.
    """
    out = {}
    for r in range(1, ws.max_row + 1):
        sku = ws.cell(r, 1).value
        qty = ws.cell(r, 2).value
        if sku is None:
            continue
        sku_str = str(sku).strip()
        if sku_str == "" or sku_str.lower() in ("sku", "vendor style"):
            continue
        # skip title row like "Depot "
        if qty is None and len(sku_str) <= 20 and sku_str.lower().strip() in ("depot", "depot ", "lowe's", "amazon", "tractor supply", "depot so"):
            continue
        try:
            q = float(qty)
        except Exception:
            continue
        # keep zeros too? We'll ignore zeros to avoid clutter
        if q == 0:
            continue
        out[sku_str] = out.get(sku_str, 0.0) + q
    return out
from pathlib import Path

APP_TITLE = "Weekly Retailer Report (Multi-week View)"
DB_PATH = "app.db"
APP_DIR = Path(__file__).resolve().parent

# -----------------------------
# Week selector (2026 only for now)
# -----------------------------
def weeks_2026():
    rows = []
    # Special partial week
    rows.append((date(2026, 1, 1), date(2026, 1, 2), "1-1 / 1-2"))
    monday = date(2026, 1, 5)
    for i in range(0, 60):
        start = monday + timedelta(weeks=i)
        end = start + timedelta(days=4)
        if start.year != 2026:
            break
        if end.year != 2026:
            end = date(2026, 12, 31)
        rows.append((start, end, f"{start.month}-{start.day} / {end.month}-{end.day}"))
        if end == date(2026, 12, 31):
            break
    return rows

# -----------------------------
# DB helpers
# -----------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS sku_mapping (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        retailer TEXT NOT NULL,
        vendor TEXT NOT NULL,
        sku TEXT NOT NULL,
        unit_price REAL,
        active INTEGER NOT NULL DEFAULT 1,
        sort_order INTEGER,
        UNIQUE(retailer, sku)
    );

    CREATE TABLE IF NOT EXISTS weekly_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        week_start TEXT NOT NULL,
        week_end TEXT NOT NULL,
        retailer TEXT NOT NULL,
        sku TEXT NOT NULL,
        units_auto REAL,
        units_override REAL,
        sales_manual REAL,
        notes TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE(week_start, retailer, sku)
    );

    CREATE INDEX IF NOT EXISTS idx_weekly_results_week_retailer
    ON weekly_results(week_start, retailer);

    CREATE TABLE IF NOT EXISTS ui_state (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)
    # In case an older DB exists, try to add unit_price (no-op if already present)
    try:
        conn.execute("ALTER TABLE sku_mapping ADD COLUMN unit_price REAL;")
    except Exception:
        pass
    conn.commit()

def mapping_count(conn):
    df = pd.read_sql_query("SELECT COUNT(*) AS n FROM sku_mapping", conn)
    return int(df.loc[0, "n"]) if not df.empty else 0

def get_ui_state(conn, key: str, default=None):
    try:
        df = pd.read_sql_query("SELECT value FROM ui_state WHERE key = ?", conn, params=(key,))
        if df.empty:
            return default
        return json.loads(df.loc[0, "value"])
    except Exception:
        return default

def set_ui_state(conn, key: str, value):
    try:
        conn.execute(
            "INSERT INTO ui_state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value))
        )
        conn.commit()
    except Exception:
        pass

def mapping_has_any_price(conn) -> bool:
    try:
        df = pd.read_sql_query("SELECT COUNT(*) AS n FROM sku_mapping WHERE unit_price IS NOT NULL", conn)
        return int(df.loc[0, "n"]) > 0
    except Exception:
        return False

def refresh_mapping_from_bundled_if_needed(conn):
    """
    If mapping exists but has no prices populated, reload from bundled Vendor-SKU Map.xlsx.
    This fixes the common case where the DB was bootstrapped from an older map without price.
    """
    if mapping_count(conn) == 0:
        return False
    if mapping_has_any_price(conn):
        return False
    # Try reading bundled map relative to app.py
    candidates = [
        APP_DIR / "Vendor-SKU Map.xlsx",
        Path("Vendor-SKU Map.xlsx"),
    ]
    for p in candidates:
        try:
            if p.exists():
                df_map = pd.read_excel(p, sheet_name=0)
                # only reload if the file actually contains a price column
                price_col = next((c for c in df_map.columns if "price" in str(c).lower()), None)
                if price_col:
                    upsert_mapping(conn, df_map)
                    return True
        except Exception:
            continue
    return False

def upsert_mapping(conn, df: pd.DataFrame):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    required = {"Retailer", "SKU", "Vendor"}
    if not required.issubset(set(df.columns)):
        raise ValueError(f"Mapping must contain columns: {sorted(required)}. Found: {list(df.columns)}")

    price_col = next((c for c in df.columns if "price" in str(c).lower()), None)

    df = df[list(required) + ([price_col] if price_col else [])].dropna(subset=["Retailer", "SKU", "Vendor"])
    df["Retailer"] = df["Retailer"].astype(str).str.strip()
    df["SKU"] = df["SKU"].astype(str).str.strip()
    df["Vendor"] = df["Vendor"].astype(str).str.strip()

    if price_col:
        # coerce to numeric
        df[price_col] = pd.to_numeric(df[price_col], errors="coerce")

    retailers = sorted(df["Retailer"].unique().tolist())
    cur = conn.cursor()
    cur.executemany("DELETE FROM sku_mapping WHERE retailer = ?", [(r,) for r in retailers])

    rows = []
    for r in retailers:
        sub = df[df["Retailer"] == r].reset_index(drop=True)
        for i, row in sub.iterrows():
            price = float(row[price_col]) if price_col and pd.notna(row[price_col]) else None
            rows.append((row["Retailer"], row["Vendor"], row["SKU"], price, 1, i + 1))

    cur.executemany("""
        INSERT INTO sku_mapping(retailer, vendor, sku, unit_price, active, sort_order)
        VALUES(?,?,?,?,?,?)
    """, rows)
    conn.commit()

def bootstrap_default_mapping(conn):
    """
    Load a bundled mapping file shipped with the app on first run (or when DB is empty).
    Works on Streamlit Cloud where the working directory can vary.
    """
    if mapping_count(conn) > 0:
        return False

    candidates = [
        APP_DIR / "Vendor-SKU Map.xlsx",
        APP_DIR / "Vendor-SKU Map - example.xlsx",
        Path("Vendor-SKU Map.xlsx"),
        Path("Vendor-SKU Map - example.xlsx"),
    ]
    for p in candidates:
        try:
            if p.exists():
                df_map = pd.read_excel(p, sheet_name=0)
                upsert_mapping(conn, df_map)
                return True
        except Exception:
            continue
    return False

    candidates = [
        APP_DIR / "Vendor-SKU Map.xlsx",
        APP_DIR / "Vendor-SKU Map - example.xlsx",
        Path("Vendor-SKU Map.xlsx"),
        Path("Vendor-SKU Map - example.xlsx"),
    ]
    for p in candidates:
        try:
            if p.exists():
                df_map = pd.read_excel(p, sheet_name=0)
                upsert_mapping(conn, df_map)
                return True
        except Exception:
            continue
    return False
    for fn in ["Vendor-SKU Map.xlsx", "Vendor-SKU Map - example.xlsx"]:
        try:
            df_map = pd.read_excel(fn, sheet_name=0)
            upsert_mapping(conn, df_map)
            return True
        except Exception:
            continue
    return False

def get_retailers(conn):
    df = pd.read_sql_query("""
        SELECT DISTINCT retailer FROM sku_mapping
        WHERE active = 1
        ORDER BY retailer
    """, conn)
    return df["retailer"].tolist()

def get_mapping_for_retailer(conn, retailer: str):
    return pd.read_sql_query("""
        SELECT vendor, sku, unit_price, sort_order
        FROM sku_mapping
        WHERE active = 1 AND retailer = ?
        ORDER BY COALESCE(sort_order, 999999), vendor, sku
    """, conn, params=(retailer,))

def get_week_records(conn, retailer: str, week_starts: list[str]):
    if not week_starts:
        return pd.DataFrame(columns=["week_start","sku","units_auto","units_override","sales_manual","notes"])
    placeholders = ",".join(["?"] * len(week_starts))
    q = f"""
        SELECT week_start, sku, units_auto, units_override, sales_manual, notes
        FROM weekly_results
        WHERE retailer = ? AND week_start IN ({placeholders})
    """
    return pd.read_sql_query(q, conn, params=[retailer] + week_starts)

def set_units_auto_from_upload(conn, week_start: date, week_end: date, retailer: str, units_by_sku: dict):
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    cur = conn.cursor()
    for sku, units in units_by_sku.items():
        sku = str(sku).strip()
        try:
            units_val = float(units)
        except Exception:
            continue
        cur.execute("""
            INSERT INTO weekly_results(week_start, week_end, retailer, sku,
                                      units_auto, units_override, sales_manual, notes, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(week_start, retailer, sku) DO UPDATE SET
                week_end=excluded.week_end,
                units_auto=excluded.units_auto,
                updated_at=excluded.updated_at
        """, (
            week_start.isoformat(), week_end.isoformat(), retailer, sku,
            units_val, None, None, None, now
        ))
    conn.commit()

# -----------------------------
# Upload parser (based on your example workbook)
# -----------------------------
def parse_weekly_workbook(file, sheet_name: str):
    wb = load_workbook(file, data_only=True)
    if sheet_name not in wb.sheetnames:
        return {}
    ws = wb[sheet_name]
    out = {}

    def add(sku, qty):
        if sku is None:
            return
        sku = str(sku).strip()
        if sku == "" or sku.lower() == "sku":
            return
        try:
            q = float(qty)
        except Exception:
            return
        out[sku] = out.get(sku, 0.0) + q

    if sheet_name in ("Depot", "Lowe's"):
        for r in range(1, ws.max_row + 1):
            add(ws.cell(r, 5).value, ws.cell(r, 6).value)  # E, F

    elif sheet_name == "Depot SO":
        for r in range(1, ws.max_row + 1):
            add(ws.cell(r, 4).value, ws.cell(r, 5).value)  # D, E

    elif sheet_name == "Amazon":
        for r in range(1, ws.max_row + 1):
            add(ws.cell(r, 3).value, ws.cell(r, 14).value)  # C, N

    elif sheet_name == "TSC":
        header_row = None
        for r in range(1, min(ws.max_row, 10) + 1):
            a = ws.cell(r, 1).value
            b = ws.cell(r, 2).value
            if isinstance(a, str) and isinstance(b, str) and "Vendor" in a and "Qty" in b:
                header_row = r
                break
        start = (header_row + 1) if header_row else 2
        for r in range(start, ws.max_row + 1):
            add(ws.cell(r, 1).value, ws.cell(r, 2).value)

    return {k: v for k, v in out.items() if v != 0}

# -----------------------------
# Build multi-week view dataframe
# -----------------------------
def build_multiweek_df(conn, retailer: str, week_meta: list[tuple[date,date,str]], display_labels: list[str], edit_label: str):
    mapping = get_mapping_for_retailer(conn, retailer)
    if mapping.empty:
        return pd.DataFrame()

    label_to_start = {lbl: start.isoformat() for start, _, lbl in week_meta}
    starts = [label_to_start[lbl] for lbl in display_labels if lbl in label_to_start]
    wk = get_week_records(conn, retailer, starts)

    # resolved units per (week_start, sku)
    if not wk.empty:
        wk["UnitsResolved"] = wk["units_override"].where(wk["units_override"].notna(), wk["units_auto"])
    else:
        wk = pd.DataFrame(columns=["week_start","sku","UnitsResolved","sales_manual","notes"])

    base = mapping.rename(columns={"vendor":"Vendor","sku":"SKU","unit_price":"Unit Price"}).copy()
    base["Unit Price"] = pd.to_numeric(base["Unit Price"], errors="coerce")

    # Add per-week columns
    for lbl in display_labels:
        ws = label_to_start.get(lbl)
        if not ws:
            base[lbl] = pd.NA
            continue
        sub = wk[wk["week_start"] == ws][["sku","UnitsResolved"]].rename(columns={"sku":"SKU", "UnitsResolved": lbl})
        base = base.merge(sub, on="SKU", how="left")

    # Add Sales/Notes for edit week only (far right)
    edit_start = label_to_start.get(edit_label)
    if edit_start and not wk.empty:
        sub2 = wk[wk["week_start"] == edit_start][["sku","sales_manual","notes"]].rename(columns={"sku":"SKU"})
        base = base.merge(sub2, on="SKU", how="left")
    else:
        base["sales_manual"] = pd.NA
        base["notes"] = pd.NA

    base.rename(columns={"sales_manual":"Sales", "notes":"Notes"}, inplace=True)

    # Total $ across displayed weeks (calculated, read-only)
    # Sum units across displayed labels * unit price per row
    units_sum = None
    for lbl in display_labels:
        col = pd.to_numeric(base[lbl], errors="coerce")
        units_sum = col if units_sum is None else units_sum.add(col, fill_value=0)
    base["Total $ (Units x Price)"] = (units_sum * base["Unit Price"]).where(base["Unit Price"].notna(), pd.NA)
    # Δ Units between the last two displayed weeks (per SKU)
    if len(display_labels) >= 2:
        prev_lbl = display_labels[-2]
        last_lbl = display_labels[-1]
        prev_vals = pd.to_numeric(base[prev_lbl], errors="coerce").fillna(0)
        last_vals = pd.to_numeric(base[last_lbl], errors="coerce").fillna(0)
        base["Δ Units (Last - Prev)"] = last_vals - prev_vals
    else:
        base["Δ Units (Last - Prev)"] = pd.NA



    # Reorder columns: Vendor, SKU, Unit Price, week cols..., Total$, Sales, Notes
    cols = ["Vendor","SKU","Unit Price"] + display_labels + ["Total $ (Units x Price)","Sales","Notes","Δ Units (Last - Prev)"]
    return base[cols]

def save_edit_week(conn, retailer: str, week_start: date, week_end: date, edit_label: str, edited_df: pd.DataFrame):
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    cur = conn.cursor()

    # The editable units are in the column named edit_label.
    for _, row in edited_df.iterrows():
        sku = str(row["SKU"]).strip()
        units_val = row.get(edit_label)
        sales_val = row.get("Sales")
        notes_val = row.get("Notes")

        # Units override stored as the edited value (can be blank to clear)
        units_override = None
        if units_val is not None and not (isinstance(units_val, float) and pd.isna(units_val)):
            try:
                units_override = float(units_val)
            except Exception:
                units_override = None

        sales_manual = None
        if sales_val is not None and not (isinstance(sales_val, float) and pd.isna(sales_val)):
            try:
                sales_manual = float(sales_val)
            except Exception:
                sales_manual = None

        notes_txt = None
        if notes_val is not None and not (isinstance(notes_val, float) and pd.isna(notes_val)) and str(notes_val).strip() != "":
            notes_txt = str(notes_val)

        cur.execute("""
            INSERT INTO weekly_results(week_start, week_end, retailer, sku,
                                      units_auto, units_override, sales_manual, notes, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(week_start, retailer, sku) DO UPDATE SET
                week_end=excluded.week_end,
                units_override=excluded.units_override,
                sales_manual=excluded.sales_manual,
                notes=excluded.notes,
                updated_at=excluded.updated_at
        """, (
            week_start.isoformat(), week_end.isoformat(), retailer, sku,
            None, units_override, sales_manual, notes_txt, now
        ))
    conn.commit()

# -----------------------------
# UI
# -----------------------------
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

conn = get_conn()
init_db(conn)
booted = bootstrap_default_mapping(conn)
refreshed_prices = refresh_mapping_from_bundled_if_needed(conn)

with st.sidebar:
    st.header("Setup (optional)")
    st.caption("Mapping is bundled. Only upload if you want to replace it.")
    st.write("✅ Loaded bundled mapping" if booted else "ℹ️ Using existing mapping in database")
    map_file = st.file_uploader("Upload Vendor-SKU Map (.xlsx) (optional)", type=["xlsx"])
    if map_file is not None:
        try:
            df_map = pd.read_excel(map_file, sheet_name=0)
            upsert_mapping(conn, df_map)
            st.success("Mapping updated.")
        except Exception as e:
            st.error(f"Mapping upload failed: {e}")

retailers = get_retailers(conn)
if not retailers:
    st.info("No mapping loaded. Ensure 'Vendor-SKU Map.xlsx' is present or upload one in the sidebar.")
    st.stop()

retailer = st.selectbox("Retailer", retailers)

week_meta = weeks_2026()
labels = [w[2] for w in week_meta]

# Multi-week display selector + edit week
# Load saved UI preferences (per retailer)
state_key = f"ui::{retailer}"
saved = get_ui_state(conn, state_key, default={}) or {}
saved_display = saved.get("display_weeks")
saved_edit = saved.get("edit_week")
default_display = labels[:9]  # partial + first 8 full weeks
# Use saved weeks if available
if isinstance(saved_display, list):
    default_display = [w for w in labels if w in saved_display] or default_display

display_weeks = st.multiselect("Weeks to display (columns)", labels, default=default_display, key="display_weeks")
display_weeks = [lbl for lbl in labels if lbl in display_weeks]  # keep chronological order

edit_index = labels.index(saved_edit) if (saved_edit in labels) else 0
edit_week = st.selectbox("Week to edit (units override + sales)", labels, index=edit_index, key="edit_week")

# Keep edit week included in display
if edit_week not in display_weeks:
    display_weeks = display_weeks + [edit_week]

# Persist selections
set_ui_state(conn, state_key, {"display_weeks": display_weeks, "edit_week": edit_week})

# Upload weekly export

tab_report, tab_top_retailer, tab_top_vendor = st.tabs(["Report", "Top 5 by Retailer", "Top 5 by Vendor"])

with tab_report:
    st.subheader("Upload units workbook (APP…) (recommended)")
    app_file = st.file_uploader("Upload the weekly APP workbook (.xlsx)", type=["xlsx"], key="app_units_upload")
    st.caption("Expected filename: 'APP M-D thru M-D.xlsx' and sheets named by retailer (Depot, Lowe's, Amazon, Tractor Supply, Depot SO). Each sheet should be 2 columns: SKU + Units.")

    parsed_label = None
    parsed_start = None
    parsed_end = None

    if app_file is not None:
        # Try to auto-detect the week from the filename
        try:
            parsed_label, parsed_start, parsed_end = parse_week_label_from_filename(getattr(app_file, "name", ""))
        except Exception:
            parsed_label, parsed_start, parsed_end = None, None, None

        if parsed_label:
            st.success(f"Detected week from filename: {parsed_label}")
            # If the detected label exists in our week list, set it as the edit week default via session state
            if "edit_week" in st.session_state:
                pass
            # show dates
            st.write(f"Start: {parsed_start.isoformat()}  |  End: {parsed_end.isoformat()}")
        else:
            st.warning("Couldn't detect week from filename. Use the 'Week to edit' selector above, or rename the file like: APP 1-5 thru 1-9.xlsx")

        # Import button
        if st.button("Import units from APP workbook into the selected Edit Week", type="primary"):
            # Determine which week to write to (prefer parsed filename if it matches edit_week)
            chosen_label = edit_week
            chosen_start, chosen_end, _ = next((a,b,l) for a,b,l in week_meta if l == chosen_label)

            # Read workbook and import every sheet as its retailer
            wb_up = load_workbook(app_file, data_only=True)
            imported = []
            skipped = []
            for sh in wb_up.sheetnames:
                retailer_name = normalize_retailer_sheet_name(sh)
                ws = wb_up[sh]
                units = parse_app_aggregated_sheet(ws)
                if not units:
                    skipped.append(sh)
                    continue
                set_units_auto_from_upload(conn, chosen_start, chosen_end, retailer_name, units)
                imported.append((retailer_name, len(units)))

            if imported:
                msg = ", ".join([f"{r} ({n})" for r,n in imported])
                st.success(f"Imported units for: {msg}")
            if skipped:
                st.caption(f"Skipped empty/unrecognized sheets: {', '.join(skipped)}")

    st.divider()

    # Build and render table
    df = build_multiweek_df(conn, retailer, week_meta, display_weeks, edit_week)
    # Optional filter: show only rows with activity (units > 0 in any displayed week OR sales entered)
    show_only_with_units = st.checkbox('Show only items with units (or sales)', value=True)
    if show_only_with_units and not df.empty:
        week_cols = [c for c in display_weeks if c in df.columns]
        # treat blanks as 0, and include rows where any week col > 0 OR sales is filled
        units_any = (df[week_cols].apply(pd.to_numeric, errors='coerce').fillna(0) > 0).any(axis=1) if week_cols else pd.Series([False]*len(df))
        sales_any = pd.to_numeric(df['Sales'], errors='coerce').fillna(0) != 0
        df = df[units_any | sales_any].reset_index(drop=True)
    if df.empty:

        st.info("No rows for this retailer in your mapping.")
        st.stop()

    # Disable columns: Vendor, SKU, Unit Price, non-edit weeks, Total$
    disabled_cols = ["Vendor","SKU","Unit Price","Total $ (Units x Price)","Δ Units (Last - Prev)"] + [w for w in display_weeks if w != edit_week]
    # Sales is far right (editable), Notes editable
    edited = st.data_editor(
        df,
        use_container_width=True,
        hide_index=True,
        disabled=disabled_cols,
        column_config={**{w: st.column_config.NumberColumn(format="%.0f") for w in display_weeks},
                      "Unit Price": st.column_config.NumberColumn(format="$%,.2f"),
                      "Total $ (Units x Price)": st.column_config.NumberColumn(format="$%,.2f"),
                      "Sales": st.column_config.NumberColumn(help="Manual sales for the edit week (optional).", format="$%,.2f"),
                      "Notes": st.column_config.TextColumn()}
    )

    c1, c2 = st.columns([1,3])
    with c1:
        if st.button("Save edits", type="primary"):
            start, end, _ = next((a,b,l) for a,b,l in week_meta if l == edit_week)
            save_edit_week(conn, retailer, start, end, edit_week, edited)
            st.success("Saved.")
    with c2:
        st.caption("Only the column for the selected edit week is editable. Sales is near the right; the far-right column shows Δ Units (last selected week minus the previous week). Use the checkbox above to hide SKUs with no units.")



    st.divider()
    st.subheader("Totals (shown rows)")

    # Totals for each displayed week
    week_cols = [c for c in display_weeks if c in edited.columns]
    unit_price = pd.to_numeric(edited["Unit Price"], errors="coerce")

    tot_units = {}
    tot_dollars = {}
    for w in week_cols:
        u = pd.to_numeric(edited[w], errors="coerce").fillna(0)
        tot_units[w] = float(u.sum())
        tot_dollars[w] = float((u * unit_price.fillna(0)).sum())

    tot_df = pd.DataFrame([tot_units, tot_dollars], index=["Total Units", "Total $"])
    # Add diff totals between last two weeks selected
    if len(week_cols) >= 2:
        prev_w, last_w = week_cols[-2], week_cols[-1]
        tot_df["Δ Units (Last - Prev)"] = [tot_units[last_w] - tot_units[prev_w], pd.NA]
        tot_df["Δ $ (Last - Prev)"] = [pd.NA, tot_dollars[last_w] - tot_dollars[prev_w]]
    else:
        tot_df["Δ Units (Last - Prev)"] = [pd.NA, pd.NA]
        tot_df["Δ $ (Last - Prev)"] = [pd.NA, pd.NA]

    st.dataframe(tot_df, use_container_width=True)


    st.caption("v1: 2026 weeks only. Year selector can be added later.")


with tab_top_retailer:
    st.subheader("Top 5 items per retailer (by Units)")
    st.caption("Uses the weeks selected in 'Weeks to display (columns)'. Totals are summed across those weeks.")

    label_to_start = {lbl: start.isoformat() for start, _, lbl in week_meta}
    selected_starts = [label_to_start[lbl] for lbl in display_weeks if lbl in label_to_start]

    if not selected_starts:
        st.info("Select at least one week in 'Weeks to display' to compute top sellers.")
    else:
        placeholders = ",".join(["?"] * len(selected_starts))
        wk = pd.read_sql_query(f"""
            SELECT week_start, retailer, sku, units_auto, units_override
            FROM weekly_results
            WHERE week_start IN ({placeholders})
        """, conn, params=selected_starts)

        if wk.empty:
            st.info("No unit data found for the selected weeks yet.")
        else:
            wk["Units"] = wk["units_override"].where(wk["units_override"].notna(), wk["units_auto"])
            wk["Units"] = pd.to_numeric(wk["Units"], errors="coerce").fillna(0)

            agg = wk.groupby(["retailer","sku"], as_index=False)["Units"].sum()

            mapping_all = pd.read_sql_query("""
                SELECT retailer, vendor, sku
                FROM sku_mapping
                WHERE active = 1
            """, conn)
            dfm = agg.merge(mapping_all, on=["retailer","sku"], how="left")
            dfm["vendor"] = dfm["vendor"].fillna("Unknown")

            for ret in sorted(dfm["retailer"].unique().tolist()):
                st.markdown(f"### {ret}")
                sub = dfm[dfm["retailer"] == ret].copy()
                top = sub.sort_values("Units", ascending=False).head(5)

                out = top.rename(columns={
                    "vendor":"Vendor",
                    "sku":"SKU",
                    "Units":"Total Units (Selected Weeks)"
                })[["SKU","Vendor","Total Units (Selected Weeks)"]]
                st.dataframe(out, use_container_width=True)

with tab_top_vendor:
    st.subheader("Top 5 items per vendor, per retailer (by Units)")
    st.caption("Uses the weeks selected in 'Weeks to display (columns)'. Totals are summed across those weeks.")

    label_to_start = {lbl: start.isoformat() for start, _, lbl in week_meta}
    selected_starts = [label_to_start[lbl] for lbl in display_weeks if lbl in label_to_start]

    if not selected_starts:
        st.info("Select at least one week in 'Weeks to display' to compute top sellers.")
    else:
        placeholders = ",".join(["?"] * len(selected_starts))
        wk = pd.read_sql_query(f"""
            SELECT week_start, retailer, sku, units_auto, units_override
            FROM weekly_results
            WHERE week_start IN ({placeholders})
        """, conn, params=selected_starts)

        if wk.empty:
            st.info("No unit data found for the selected weeks yet.")
        else:
            wk["Units"] = wk["units_override"].where(wk["units_override"].notna(), wk["units_auto"])
            wk["Units"] = pd.to_numeric(wk["Units"], errors="coerce").fillna(0)

            agg = wk.groupby(["retailer","sku"], as_index=False)["Units"].sum()

            mapping_all = pd.read_sql_query("""
                SELECT retailer, vendor, sku
                FROM sku_mapping
                WHERE active = 1
            """, conn)
            dfm = agg.merge(mapping_all, on=["retailer","sku"], how="left")
            dfm["vendor"] = dfm["vendor"].fillna("Unknown")

            for ret in sorted(dfm["retailer"].unique().tolist()):
                st.markdown(f"### {ret}")
                subr = dfm[dfm["retailer"] == ret].copy()

                for vend in sorted(subr["vendor"].unique().tolist()):
                    subv = subr[subr["vendor"] == vend].copy()
                    if subv.empty:
                        continue
                    top = subv.sort_values("Units", ascending=False).head(5)

                    out = top.rename(columns={
                        "sku":"SKU",
                        "Units":"Total Units (Selected Weeks)"
                    })[["SKU","Total Units (Selected Weeks)"]]

                    st.write(f"**{vend}**")
                    st.dataframe(out, use_container_width=True)
