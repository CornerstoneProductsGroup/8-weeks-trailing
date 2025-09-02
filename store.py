
from __future__ import annotations
from pathlib import Path
import json
import pandas as pd

DATA = Path(__file__).parent / "data"
CATALOG = DATA / "catalog.parquet"
FACTS = DATA / "weekly_facts.parquet"
WEEKS_ORDER = DATA / "weeks_order.json"

def _ensure():
    DATA.mkdir(exist_ok=True)

def load_catalog() -> pd.DataFrame:
    _ensure()
    if CATALOG.exists():
        return pd.read_parquet(CATALOG)
    return pd.DataFrame(columns=["RetailerGroup","SKU"])

def save_catalog(df: pd.DataFrame) -> None:
    _ensure()
    df = df[["RetailerGroup","SKU"]].dropna(subset=["RetailerGroup","SKU"]).drop_duplicates()
    df.to_parquet(CATALOG, index=False)

def load_facts() -> pd.DataFrame:
    _ensure()
    if FACTS.exists():
        return pd.read_parquet(FACTS)
    return pd.DataFrame(columns=["WeekLabel","RetailerGroup","SKU","Units"])

def save_facts(df: pd.DataFrame) -> None:
    _ensure()
    cols = ["WeekLabel","RetailerGroup","SKU","Units"]
    df = df[cols].copy()
    df["Units"] = pd.to_numeric(df["Units"], errors="coerce").fillna(0.0)
    df.to_parquet(FACTS, index=False)

def load_weeks_order() -> list[str]:
    _ensure()
    if WEEKS_ORDER.exists():
        return json.loads(WEEKS_ORDER.read_text())
    return []

def save_weeks_order(labels: list[str]) -> None:
    _ensure()
    WEEKS_ORDER.write_text(json.dumps(labels, indent=2))

def ensure_week_in_order(week_label: str) -> None:
    order = load_weeks_order()
    if week_label not in order:
        order.append(week_label)
        save_weeks_order(order)
