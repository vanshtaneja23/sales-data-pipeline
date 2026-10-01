"""Turn all-string source frames into contract-typed frames.

Policy: anything that would make a value *wrong* (unparseable number, unknown code, out-of-range,
conflicting duplicate key) raises DataContractError with examples. Anything that is a known,
explainable source quirk is normalised or flagged and counted in `issues`, which is written
to ops.load_audit so the quirk stays visible instead of disappearing into the warehouse.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from sales_pipeline.contracts import Column, Contract


class DataContractError(ValueError):
    """Source data violates its contract; the load must not proceed."""


@dataclass
class CleanResult:
    frame: pd.DataFrame
    rows_in: int
    issues: dict[str, int] = field(default_factory=dict)

    @property
    def rows_out(self) -> int:
        return len(self.frame)


STATE_HOLIDAY_CODES = {"0": "none", "a": "public", "b": "easter", "c": "christmas"}
_BOOL = {"0": False, "1": True}


def _fail(col: str, problem: str, bad: pd.Series) -> DataContractError:
    sample = bad.head(5).tolist()
    return DataContractError(f"{col}: {len(bad)} {problem}; e.g. {sample}")


def cast_column(raw: pd.Series, col: Column) -> pd.Series:
    """Cast one all-string column to its contract type, enforcing nullability, codes and ranges."""
    s = raw.astype("string").str.strip()
    missing = s.isna() | s.eq("")
    if missing.any() and not col.nullable:
        raise _fail(col.name, "empty values in non-nullable column", raw[missing])
    present = s.mask(missing)

    if col.type in ("integer", "smallint", "numeric"):
        num = pd.to_numeric(present, errors="coerce")
        bad = ~missing & num.isna()
        if bad.any():
            raise _fail(col.name, "non-numeric values", raw[bad])
        if col.type != "numeric":
            frac = num.notna() & (num % 1 != 0)
            if frac.any():
                raise _fail(col.name, "non-integer values", raw[frac])
            out = num.astype("Int64")
        else:
            out = num.astype("Float64")
    elif col.type == "date":
        out = pd.to_datetime(present, format="%Y-%m-%d", errors="coerce")
        bad = ~missing & out.isna()
        if bad.any():
            raise _fail(col.name, "values not in YYYY-MM-DD format", raw[bad])
    elif col.type == "boolean":
        out = present.map(_BOOL).astype("boolean")
        bad = ~missing & out.isna()
        if bad.any():
            raise _fail(col.name, "values not in {0,1}", raw[bad])
    else:  # text
        out = present

    if col.allowed is not None:
        bad = out.notna() & ~out.isin(col.allowed)
        if bad.any():
            raise _fail(col.name, f"values outside allowed set {list(col.allowed)}", raw[bad])
    if col.min is not None:
        bad = out.notna() & (out < col.min)
        if bad.any():
            raise _fail(col.name, f"values below min {col.min}", raw[bad])
    if col.max is not None:
        bad = out.notna() & (out > col.max)
        if bad.any():
            raise _fail(col.name, f"values above max {col.max}", raw[bad])
    return out


def _apply_contract(df: pd.DataFrame, contract: Contract) -> pd.DataFrame:
    missing = [c for c in contract.source_columns if c not in df.columns]
    if missing:
        raise DataContractError(f"{contract.source}: source is missing columns {missing}")
    return pd.DataFrame(
        {c.name: cast_column(df[c.source], c) for c in contract.columns if c.source}
    )


def _dedupe(df: pd.DataFrame, contract: Contract, issues: dict[str, int]) -> pd.DataFrame:
    exact = df.duplicated()
    issues["exact_duplicates_dropped"] = int(exact.sum())
    df = df.loc[~exact]
    conflicting = df.duplicated(list(contract.primary_key), keep=False)
    if conflicting.any():
        keys = df.loc[conflicting, list(contract.primary_key)].drop_duplicates()
        raise DataContractError(
            f"{contract.source}: {len(keys)} primary keys appear with conflicting values; "
            f"e.g. {keys.head(5).to_dict('records')}"
        )
    return df.reset_index(drop=True)


def clean_sales(raw: pd.DataFrame, contract: Contract) -> CleanResult:
    issues: dict[str, int] = {}
    df = raw.copy()
    # Source mixes int 0 and string "0" for "no holiday"; read as str both become "0".
    df["StateHoliday"] = df["StateHoliday"].str.strip().map(
        lambda v: STATE_HOLIDAY_CODES.get(v, v)
    )
    out = _apply_contract(df, contract)

    # DayOfWeek is redundant with Date; if they disagree, one of them is wrong -> stop.
    iso_dow = out["sales_date"].dt.dayofweek + 1
    mismatched = iso_dow != out["day_of_week"]
    if mismatched.any():
        raise _fail("day_of_week", "rows disagree with sales_date's weekday", raw.loc[mismatched, "Date"])

    out["is_zero_sales_while_open"] = (out["is_open"] & out["sales_amount"].eq(0)).astype("boolean")
    issues["zero_sales_while_open"] = int(out["is_zero_sales_while_open"].sum())
    issues["closed_with_sales"] = int((~out["is_open"] & out["sales_amount"].gt(0)).sum())
    out = _dedupe(out, contract, issues)
    return CleanResult(out, rows_in=len(raw), issues=issues)


def clean_stores(raw: pd.DataFrame, contract: Contract) -> CleanResult:
    issues: dict[str, int] = {}
    df = raw.copy()
    # The source abbreviates September as "Sept" while every other month uses three letters.
    sept = df["PromoInterval"].str.contains("Sept", regex=False)
    issues["promo_interval_sept_normalised"] = int(sept.sum())
    df["PromoInterval"] = df["PromoInterval"].str.replace("Sept", "Sep", regex=False)
    out = _apply_contract(df, contract)

    since_cols = ["promo2_since_week", "promo2_since_year", "promo2_interval"]
    no_promo_with_dates = ~out["has_promo2"] & out[since_cols].notna().any(axis=1)
    promo_without_dates = out["has_promo2"] & out[since_cols].isna().any(axis=1)
    issues["promo2_flag_inconsistent"] = int((no_promo_with_dates | promo_without_dates).sum())
    issues["competition_distance_missing"] = int(out["competition_distance_m"].isna().sum())
    out = _dedupe(out, contract, issues)
    return CleanResult(out, rows_in=len(raw), issues=issues)


def clean_store_states(raw: pd.DataFrame, contract: Contract) -> CleanResult:
    issues: dict[str, int] = {}
    out = _apply_contract(raw, contract)
    # "HB,NI": the source couldn't tell Bremen from Lower Saxony for these stores. Keep the
    # honest ambiguous code rather than guessing one state.
    out["is_state_ambiguous"] = out["state_code"].str.contains(",", regex=False).astype("boolean")
    issues["ambiguous_state"] = int(out["is_state_ambiguous"].sum())
    out = _dedupe(out, contract, issues)
    return CleanResult(out, rows_in=len(raw), issues=issues)


CLEANERS: dict[str, Callable[[pd.DataFrame, Contract], CleanResult]] = {
    "sales": clean_sales,
    "stores": clean_stores,
    "store_states": clean_store_states,
}
