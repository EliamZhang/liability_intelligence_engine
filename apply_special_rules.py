import csv
import os

import pandas as pd


def normalize_text(value):
    if pd.isna(value):
        return ""
    return str(value)


def parse_amount(row):
    value = row.get("amount")
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def apply_rules(row):
    counterparty = normalize_text(row.get("counterparty", ""))
    text = normalize_text(row.get("text")).lower()
    amount = parse_amount(row)
    is_dishonours = normalize_text(row.get("is_dishonours")).strip().lower()
    dr_cr = normalize_text(row.get("dr_cr")).strip().lower()

    if (
        counterparty == "Cash Converters"
        and is_dishonours != "yes"
        and dr_cr == "credit"
        and amount is not None
        and 50 < amount < 200
    ):
        row["product_type"] = "wage_advance"

    if counterparty == "Credit Corp":
        if "wizit" in text or "wizitca" in text:
            row["product_type"] = "bnpl"
        elif "pup" in text:
            row["product_type"] = "loc"
        elif "ccc" in text:
            row["product_type"] = "personal_loan"


def apply_special_rules(df):
    output = df.copy()
    for row_id, row in output.iterrows():
        updated_row = row.to_dict()
        apply_rules(updated_row)
        for column, value in updated_row.items():
            output.at[row_id, column] = value
    return output


def process_file(input_file, output_file):
    with open(input_file, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
        fieldnames = list(rows[0]) if rows else []

    for row in rows:
        apply_rules(row)

    temp_file = output_file + ".tmp"
    with open(temp_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp_file, output_file)
