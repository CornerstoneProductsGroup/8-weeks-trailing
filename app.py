import streamlit as st
import pandas as pd
import sqlite3
from datetime import date, datetime, timedelta
import re
from openpyxl import load_workbook

APP_TITLE = "Weekly Retailer Report (Units + Manual Sales)"
DB_PATH = "app.db"

# -----------------------------
# Week selector (2026 only for now)
# -----------------------------
def weeks_2026():
    labels = []
    # Special first partial week
    labels.append((date(2026,1,1), date(2026,1,2), "1-1 / 1-2"))
    monday = date(2026,1,5)
    for i in range(0, 60):  # more than enough, we'll stop once we cross 2026
        start = monday + timedelta(weeks=i)
        end = start + timedelta(days=4)
        if start.year != 2026:
            break
        if end.year != 2026:
            end = date(2026,12,31)
        labels.append((start, end, f"{start.month}-{start.day} / {end.month}-{end.day}"))
        if end == date(2026,12,31):
            break
    return labels

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
    """)
    conn.commit()

def upsert_mapping(conn, df: pd.DataFrame):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    required = {"Retailer","SKU","Vendor"}
    if not required.issubset(set(df.columns)):
        raise ValueError(f"Mapping must contain columns: {sorted(required)}. Found: {list(df.columns)}")

    df = df[["Retailer","SKU","Vendor"]].dropna()
    df["Retailer"] = df["Retailer"].astype(str).str.strip()
    df["SKU"] = df["SKU"].astype(str).str.strip()
    df["Vendor"] = df["Vendor"].astype(str).str.strip()

    # Replace mapping for retailers present in upload
    retailers = sorted(df["Retailer"].unique().tolist())
    cur = conn.cursor()
    cur.executemany("DELETE FROM sku_mapping WHERE retailer = ?", [(r,) for r in retailers])

    rows = []
    for r in retailers:
        sub = df[df["Retailer"]==r].reset_index(drop=True)
        for i, row in sub.iterrows():
            rows.append((row["Retailer"], row["Vendor"], row["SKU"], 1, i+1))
    cur.executemany("""
        INSERT INTO sku_mapping(retailer, vendor, sku, active, sort_order)
        VALUES(?,?,?,?,?)
    """, rows)
    conn.commit()

def get_retailers(conn):
    df = pd.read_sql_query("""
        SELECT DISTINCT retailer FROM sku_mapping
        WHERE active = 1
        ORDER BY retailer
    """, conn)
    return df["retailer"].tolist()

def get_mapping_for_retailer(conn, retailer: str):
    return pd.read_sql_query("""
        SELECT vendor, sku, sort_order
        FROM sku_mapping
        WHERE active = 1 AND retailer = ?
        ORDER BY COALESCE(sort_order, 999999), vendor, sku
    """, conn, params=(retailer,))

def get_week_df(conn, retailer: str, week_start: date, week_end: date):
    mapping = get_mapping_for_retailer(conn, retailer)
    if mapping.empty:
        return pd.DataFrame(columns=["Vendor","SKU","Units","Sales","Notes","_units_auto","_units_override"])

    wk = pd.read_sql_query("""
        SELECT sku,
               units_auto,
               units_override,
               sales_manual,
               notes
        FROM weekly_results
        WHERE retailer = ? AND week_start = ?
    """, conn, params=(retailer, week_start.isoformat()))

    out = mapping.merge(wk, how="left", left_on="sku", right_on="sku")
    out.rename(columns={"vendor":"Vendor","sku":"SKU"}, inplace=True)

    out["_units_auto"] = out["units_auto"]
    out["_units_override"] = out["units_override"]

    # Display Units = override if present else auto
    out["Units"] = out["units_override"].where(out["units_override"].notna(), out["units_auto"])
    out["Sales"] = out["sales_manual"]
    out["Notes"] = out["notes"]

    # Keep only display columns plus hidden helper columns
    out = out[["Vendor","SKU","Units","Sales","Notes","_units_auto","_units_override"]]
    return out

def save_week_edits(conn, retailer: str, week_start: date, week_end: date, edited_df: pd.DataFrame):
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    cur = conn.cursor()

    for _, row in edited_df.iterrows():
        sku = str(row["SKU"]).strip()
        units = row.get("Units")
        sales = row.get("Sales")
        notes = row.get("Notes")

        # units_override: if user typed a value different than units_auto or if units_auto is null
        units_auto = row.get("_units_auto")
        if pd.isna(units):
            units_override = None
        else:
            # store as override if it differs from auto OR auto is missing
            if pd.isna(units_auto) or (not pd.isna(units_auto) and float(units) != float(units_auto)):
                units_override = float(units)
            else:
                units_override = None  # same as auto -> clear override

        sales_manual = None if pd.isna(sales) else float(sales)
        notes_txt = None if (notes is None or (isinstance(notes,float) and pd.isna(notes)) or str(notes).strip()=="") else str(notes)

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

def weeks_with_data(conn, retailer: str):
    df = pd.read_sql_query("""
        SELECT DISTINCT week_start FROM weekly_results
        WHERE retailer = ?
        ORDER BY week_start
    """, conn, params=(retailer,))
    return set(df["week_start"].tolist())

# -----------------------------
# Upload parser for the weekly export workbook
# (based on your example format)
# -----------------------------
def parse_weekly_workbook(file_path_or_bytes, sheet_name: str):
    """
    Returns dict: sku -> units
    Supported:
      - "Depot" and "Lowe's": data rows with SKU in col E, units in col F
      - "Depot SO": SKU in col D, units in col E
      - "Amazon": SKU in col C, units in col N (row 1 has labels)
      - "TSC": header row with "Vendor Style" and "Qty Ordered" in A1/B1; data down the sheet with blanks between
    """
    wb = load_workbook(file_path_or_bytes, data_only=True)
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
        if sku not in out:
            out[sku] = 0.0
        out[sku] += q

    if sheet_name in ("Depot", "Lowe's"):
        for r in range(1, ws.max_row+1):
            sku = ws.cell(r, 5).value  # E
            qty = ws.cell(r, 6).value  # F
            add(sku, qty)

    elif sheet_name == "Depot SO":
        for r in range(1, ws.max_row+1):
            sku = ws.cell(r, 4).value  # D
            qty = ws.cell(r, 5).value  # E
            add(sku, qty)

    elif sheet_name == "Amazon":
        for r in range(1, ws.max_row+1):
            sku = ws.cell(r, 3).value   # C
            qty = ws.cell(r, 14).value  # N
            add(sku, qty)

    elif sheet_name == "TSC":
        # Find header row (usually row 1)
        header_row = None
        for r in range(1, min(ws.max_row, 10)+1):
            a = ws.cell(r,1).value
            b = ws.cell(r,2).value
            if isinstance(a, str) and isinstance(b, str) and "Vendor" in a and "Qty" in b:
                header_row = r
                break
        start = (header_row + 1) if header_row else 2
        for r in range(start, ws.max_row+1):
            sku = ws.cell(r,1).value
            qty = ws.cell(r,2).value
            add(sku, qty)

    return {k: v for k, v in out.items() if v != 0}

# -----------------------------
# UI
# -----------------------------
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

conn = get_conn()
init_db(conn)

with st.sidebar:
    st.header("Setup")
    st.caption("Upload your Vendor-SKU Map once. After that, just upload weekly retailer exports and enter sales.")
    map_file = st.file_uploader("Upload Vendor-SKU Map (.xlsx)", type=["xlsx"])
    if map_file is not None:
        try:
            df_map = pd.read_excel(map_file, sheet_name=0)
            upsert_mapping(conn, df_map)
            st.success("Mapping updated.")
        except Exception as e:
            st.error(f"Mapping upload failed: {e}")

retailers = get_retailers(conn)
if not retailers:
    st.info("Upload your Vendor-SKU Map to begin. It must include columns: Retailer, SKU, Vendor.")
    st.stop()

# Retailer selection
retailer = st.selectbox("Retailer", retailers)

# Week selection
week_rows = weeks_2026()
if not week_rows:
    st.error('No weeks generated.'); st.stop()
week_labels = [w[2] for w in week_rows]
week_label = st.selectbox("Week", week_labels, index=0)

sel = [t for t in week_rows if t[2] == week_label]
if not sel:
    # Fallback to first week if something went odd
    week_start, week_end, _ = week_rows[0]
else:
    week_start, week_end, _ = sel[0]

# show data badge + filter
existing = weeks_with_data(conn, retailer)
colA, colB, colC = st.columns([1,1,2])
with colA:
    st.write(f"**Week Start:** {week_start.isoformat()}")
with colB:
    st.write(f"**Week End:** {week_end.isoformat()}")
with colC:
    st.write("✅ **Data exists**" if week_start.isoformat() in existing else "⬜ **Empty week**")

st.divider()

# Upload weekly export file (optional)
st.subheader("1) Upload weekly retailer export (optional)")
up = st.file_uploader("Upload weekly export workbook (.xlsx)", type=["xlsx"], key="weekly_upload")
parse_hint = {
    "Depot": "Sheet 'Depot' – SKU col E, Qty col F",
    "Lowe's": "Sheet \"Lowe's\" – SKU col E, Qty col F",
    "Depot SO": "Sheet 'Depot SO' – SKU col D, Qty col E",
    "Amazon": "Sheet 'Amazon' – SKU col C, Units col N",
    "TSC": "Sheet 'TSC' – columns 'Vendor Style' and 'Qty Ordered'"
}
st.caption("Supported formats (based on your example workbook): " + " | ".join([f"{k}" for k in parse_hint.keys()]))

if up is not None:
    # Decide which sheet to parse based on retailer name heuristics
    sheet_map = {
        "Depot": "Depot",
        "The Home Depot": "Depot",
        "Home Depot": "Depot",
        "Depot SO": "Depot SO",
        "Lowe's": "Lowe's",
        "Lowes": "Lowe's",
        "Amazon": "Amazon",
        "TSC": "TSC",
        "Tractor Supply": "TSC"
    }
    # match by contains
    chosen_sheet = None
    for k, v in sheet_map.items():
        if k.lower() in retailer.lower():
            chosen_sheet = v
            break
    # if retailer is literally "Depot" etc, this will work; else allow manual sheet pick
    try:
        wb = load_workbook(up, read_only=True, data_only=True)
        sheets = wb.sheetnames
    except Exception:
        sheets = []

    if chosen_sheet is None or chosen_sheet not in sheets:
        chosen_sheet = st.selectbox("Select which sheet in the upload contains this retailer's data", sheets)

    if chosen_sheet:
        units = parse_weekly_workbook(up, chosen_sheet)
        if units:
            set_units_auto_from_upload(conn, week_start, week_end, retailer, units)
            st.success(f"Loaded units for {len(units)} SKUs from '{chosen_sheet}' into {retailer} for {week_label}.")
        else:
            st.warning(f"No units found to import from '{chosen_sheet}'. (Format might differ.)")

st.divider()

# Editable table
st.subheader("2) Enter Sales (and optionally override Units)")
df = get_week_df(conn, retailer, week_start, week_end)

if df.empty:
    st.info("No rows for this retailer. Check your Vendor-SKU Map.")
    st.stop()

# Data editor: lock Vendor & SKU, allow Units/Sales/Notes
edited = st.data_editor(
    df,
    use_container_width=True,
    hide_index=True,
    disabled=["Vendor","SKU","_units_auto","_units_override"],
    column_config={
        "Units": st.column_config.NumberColumn(help="Auto-filled from upload. You can type a number to override.", format="%.0f"),
        "Sales": st.column_config.NumberColumn(help="Manual sales for the week (dollars).", format="%.2f"),
        "Notes": st.column_config.TextColumn(help="Optional notes.")
    }
)

save_col1, save_col2 = st.columns([1,3])
with save_col1:
    if st.button("Save edits", type="primary"):
        save_week_edits(conn, retailer, week_start, week_end, edited)
        st.success("Saved.")
with save_col2:
    st.caption("Tip: You can paste a column of Sales values directly into the table. Units will keep auto values unless you change them.")

# Unmapped SKUs report (from upload vs mapping) – shown if there is uploaded auto data not in mapping
st.divider()
st.subheader("Unmapped SKU check")
mapped = set(get_mapping_for_retailer(conn, retailer)["sku"].astype(str).tolist())
auto = pd.read_sql_query("""
    SELECT sku, units_auto FROM weekly_results
    WHERE retailer=? AND week_start=? AND units_auto IS NOT NULL
""", conn, params=(retailer, week_start.isoformat()))
if auto.empty:
    st.caption("No imported auto-units for this week (or none for this retailer).")
else:
    auto_skus = set(auto["sku"].astype(str).tolist())
    missing = sorted(list(auto_skus - mapped))
    if missing:
        st.warning(f"{len(missing)} SKU(s) found in upload but missing from mapping (so they won't show in the main table). Add them to the Vendor-SKU Map and re-upload it.")
        st.dataframe(pd.DataFrame({"Missing SKU": missing}))
    else:
        st.success("All imported SKUs are present in your mapping.")

st.caption("v1: 2026 weeks only (year selector can be added later).")
