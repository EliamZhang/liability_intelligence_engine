import csv
import os


def parse_amount(row):
    value = row.get("amount")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def apply_rules(row):
    counterparty = row.get("counterparty", "")
    text = (row.get("text") or "").lower()
    amount = parse_amount(row)
    is_dishonours = (row.get("is_dishonours") or "").strip().lower()
    dr_cr = (row.get("dr_cr") or "").strip().lower()

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
