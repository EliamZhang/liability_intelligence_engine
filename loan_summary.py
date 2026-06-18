"""Build loan-level summary output sheets."""

from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re

import pandas as pd


LOAN_SUMMARY_SHEET_NAME = "贷款总结"
TRANSACTION_SHEET_NAME = "交易明细"

SUMMARY_COLUMNS = [
    "final_product_type",
    "stream_id",
    "counterparty",
    "transaction_start_date",
    "transaction_end_date",
    "status",
    "funded_amount",
    "repaid_amount",
    "repayment_amount",
    "recent_fn_repay_amount",
    "frequency",
    "frequency_day",
    "predicted_closing_date",
]

DUE_DATE_COLUMN_CANDIDATES = [
    "scheduled_due_date",
    "repayment_due_date",
    "due_date",
    "scheduled_repayment_date",
    "repayment_date",
]

BNPL_LIMIT_COLUMNS = ["counterparty", "bnpl_max_fn_limit"]


def parse_decimal_amount(value: object) -> Decimal | None:
    if pd.isna(value):
        return None

    text = str(value).strip().replace(",", "")
    if not text:
        return None

    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def normalize_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_match_key(value: object) -> str:
    return normalize_text(value).casefold()


def stream_base(stream_id: object) -> str:
    text = normalize_text(stream_id)
    if not text:
        return ""
    return re.sub(r"[-_]\d+$", "", text).replace("-", "_")


def derive_final_product_type(product_type: object, stream_id: object) -> object:
    product = normalize_text(product_type)
    base = stream_base(stream_id)
    if not product or not base:
        return pd.NA

    if base in {"bnpl", "wage_advance", "bank", "loc"}:
        return base
    return f"{product}_{base}"


def ensure_final_product_type(df: pd.DataFrame) -> pd.DataFrame:
    output = df.copy()
    if "final_product_type" not in output.columns:
        output["final_product_type"] = [
            derive_final_product_type(product_type, stream_id)
            for product_type, stream_id in zip(
                output.get("product_type", pd.Series(index=output.index)),
                output.get("stream_id", pd.Series(index=output.index)),
            )
        ]
    return output


def load_bnpl_maximum_limits(
    limits_file: str | Path = "resources/bnpl_maximum_limits.csv",
) -> pd.DataFrame:
    path = Path(limits_file)
    if not path.exists():
        return pd.DataFrame(columns=BNPL_LIMIT_COLUMNS)

    if path.suffix.lower() in {".xlsx", ".xls"}:
        limits = pd.read_excel(path)
    else:
        limits = pd.read_csv(path, encoding="utf-8-sig")

    if set(BNPL_LIMIT_COLUMNS).issubset(limits.columns):
        limits = limits[BNPL_LIMIT_COLUMNS].copy()
    elif {"rate_type", "counterparty", "value"}.issubset(limits.columns):
        limits = (
            limits[
                limits["rate_type"]
                .astype("string")
                .str.strip()
                .str.casefold()
                .eq("bnpl_max_fn_limit")
            ][["counterparty", "value"]]
            .copy()
            .rename(columns={"value": "bnpl_max_fn_limit"})
        )
    else:
        missing_columns = set(BNPL_LIMIT_COLUMNS).difference(limits.columns)
        raise ValueError(
            "bnpl_maximum_limits is missing required columns: "
            + ", ".join(sorted(missing_columns))
        )

    limits["_counterparty_key"] = limits["counterparty"].map(normalize_match_key)
    limits["_limit_amount"] = limits["bnpl_max_fn_limit"].map(parse_decimal_amount)
    limits = limits.drop_duplicates(subset=["_counterparty_key"], keep="first")
    return limits


def is_yes(value: object) -> bool:
    return normalize_text(value).casefold() == "yes"


def sorted_stream_transactions(group: pd.DataFrame) -> pd.DataFrame:
    return group.sort_values(
        ["_transaction_date", "_row_order"],
        kind="stable",
        na_position="last",
    )


def mark_failed_repayments(group: pd.DataFrame) -> pd.Series:
    ordered = sorted_stream_transactions(group)
    failed = pd.Series(False, index=group.index, dtype=bool)

    row_ids = ordered.index.tolist()
    for position, row_id in enumerate(row_ids[:-1]):
        row = ordered.loc[row_id]
        next_row = ordered.loc[row_ids[position + 1]]
        if normalize_text(row.get("dr_cr")).casefold() != "debit":
            continue
        if is_yes(next_row.get("is_dishonours")):
            failed.at[row_id] = True

    return failed


def effective_repayment_date(row: pd.Series, due_date_columns: list[str]) -> pd.Timestamp | pd.NaT:
    for column in due_date_columns:
        due_date = pd.to_datetime(row.get(column), errors="coerce")
        if not pd.isna(due_date):
            return due_date
    return row.get("_transaction_date", pd.NaT)


def calculate_frequency_day(
    valid_debits: pd.DataFrame,
    due_date_columns: list[str],
) -> str | None:
    if valid_debits.empty:
        return None

    weekdays: list[int] = []
    for _, row in valid_debits.iterrows():
        repayment_date = effective_repayment_date(row, due_date_columns)
        if pd.isna(repayment_date):
            continue
        weekdays.append(int(repayment_date.dayofweek))

    if not weekdays:
        return None

    counts = Counter(weekdays)
    top_count = max(counts.values())
    for weekday in weekdays:
        if counts[weekday] == top_count:
            return (pd.Timestamp("2024-01-01") + pd.Timedelta(days=weekday)).day_name()
    return None


def build_bnpl_summary(
    df: pd.DataFrame,
    limits: pd.DataFrame | None = None,
    as_of_date: date | None = None,
) -> pd.DataFrame:
    """Return one BNPL summary row per final_product_type + stream_id."""

    as_of_date = as_of_date or date.today()
    output = ensure_final_product_type(df)
    output["_row_order"] = range(len(output))
    output["_transaction_date"] = pd.to_datetime(
        output["transaction_date"],
        errors="coerce",
    )

    bnpl = output[
        output["final_product_type"].astype("string").str.casefold().eq("bnpl")
        & output["stream_id"].notna()
        & output["stream_id"].astype("string").str.strip().ne("")
    ].copy()

    if bnpl.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)

    due_date_columns = [
        column for column in DUE_DATE_COLUMN_CANDIDATES if column in bnpl.columns
    ]
    limits = limits if limits is not None else load_bnpl_maximum_limits()
    limits_by_counterparty = (
        limits.set_index("_counterparty_key")["_limit_amount"].to_dict()
        if "_counterparty_key" in limits.columns
        else {}
    )

    summary_rows: list[dict[str, object]] = []

    group_columns = ["final_product_type", "stream_id"]
    for (final_product_type, stream_id_value), group in bnpl.groupby(
        group_columns,
        dropna=False,
        sort=False,
    ):
        failed_repayments = mark_failed_repayments(group)

        debit_mask = group["dr_cr"].astype("string").str.casefold().eq("debit")
        valid_debits = group.loc[debit_mask & ~failed_repayments]

        repaid_amount = sum(
            abs(amount)
            for amount in valid_debits["amount"].map(parse_decimal_amount)
            if amount is not None
        )

        frequency_day_date = calculate_frequency_day(
            sorted_stream_transactions(valid_debits),
            due_date_columns,
        )

        summary_rows.append(
            {
                "final_product_type": final_product_type,
                "stream_id": stream_id_value,
                "counterparty": normalize_text(group["counterparty"].iloc[0]),
                "transaction_start_date": group["_transaction_date"].min(),
                "transaction_end_date": group["_transaction_date"].max(),
                "status": "Closed",
                "funded_amount": 0,
                "repaid_amount": float(repaid_amount),
                "repayment_amount": None,
                "recent_fn_repay_amount": None,
                "frequency": "fortnightly",
                "frequency_day": frequency_day_date,
                "predicted_closing_date": None,
            }
        )

    summary = pd.DataFrame(summary_rows, columns=SUMMARY_COLUMNS)

    for counterparty, counterparty_rows in summary.groupby(
        "counterparty",
        dropna=False,
        sort=False,
    ):
        sorted_rows = counterparty_rows.sort_values(
            ["transaction_end_date", "transaction_start_date", "stream_id"],
            ascending=[False, False, True],
            kind="stable",
            na_position="last",
        )
        latest_index = sorted_rows.index[0]
        end_date = summary.at[latest_index, "transaction_end_date"]
        if not pd.isna(end_date) and (as_of_date - end_date.date()).days <= 33:
            summary.at[latest_index, "status"] = "Ongoing"

    for row_id, row in summary.iterrows():
        counterparty_key = normalize_match_key(row["counterparty"])
        limit_amount = limits_by_counterparty.get(counterparty_key)

        if row["status"] == "Closed":
            recent_fn_repay_amount = Decimal("0")
        elif limit_amount is not None:
            recent_fn_repay_amount = limit_amount
        else:
            recent_fn_repay_amount = None

        numeric_recent = (
            float(recent_fn_repay_amount)
            if recent_fn_repay_amount is not None
            else None
        )
        summary.at[row_id, "recent_fn_repay_amount"] = numeric_recent
        summary.at[row_id, "repayment_amount"] = numeric_recent

    summary["transaction_start_date"] = summary["transaction_start_date"].dt.date
    summary["transaction_end_date"] = summary["transaction_end_date"].dt.date
    return summary[SUMMARY_COLUMNS]


def write_loan_summary_workbook(
    transactions_csv: str | Path,
    workbook_file: str | Path,
    limits_file: str | Path = "resources/bnpl_maximum_limits.csv",
) -> None:
    """Create or update the standard output workbook.

    ``交易明细`` and ``贷款总结`` are generated sheets and are replaced on each
    run. Any other sheets in an existing workbook are preserved for future
    product summaries or review output.
    """

    workbook_path = Path(workbook_file)
    workbook_path.parent.mkdir(parents=True, exist_ok=True)

    transactions = pd.read_csv(transactions_csv, encoding="utf-8-sig")
    transactions = ensure_final_product_type(transactions)
    limits = load_bnpl_maximum_limits(limits_file)
    summary = build_bnpl_summary(transactions, limits=limits)

    mode = "a" if workbook_path.exists() else "w"
    writer_kwargs: dict[str, object] = {"engine": "openpyxl", "mode": mode}
    if mode == "a":
        writer_kwargs["if_sheet_exists"] = "replace"

    try:
        with pd.ExcelWriter(workbook_path, **writer_kwargs) as writer:
            transactions.to_excel(
                writer,
                index=False,
                sheet_name=TRANSACTION_SHEET_NAME,
            )
            summary.to_excel(
                writer,
                index=False,
                sheet_name=LOAN_SUMMARY_SHEET_NAME,
            )
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot update {workbook_path}. Close the workbook and rerun."
        ) from exc
