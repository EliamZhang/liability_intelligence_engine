import csv

import pandas as pd


def clean_fieldnames(rows, fieldnames):
    valid_fieldnames = [name for name in fieldnames if (name or "").strip()]
    for row in rows:
        for key in list(row):
            if not (key or "").strip():
                row.pop(key, None)
    return valid_fieldnames


def load_rules(rules_file):
    keyword_rules = []
    with open(rules_file, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            counterparty = row.get("counterparty")
            product_type = row.get("product_type", "")
            if counterparty and row.get("keyword"):
                for keyword in row["keyword"].split(";"):
                    keyword = keyword.strip().lower()
                    if keyword:
                        keyword_rules.append((keyword, counterparty, product_type))
    return keyword_rules


def match_text(text, keyword_rules):
    text = "" if pd.isna(text) else str(text).lower()
    for keyword, counterparty, product_type in keyword_rules:
        if keyword in text:
            return counterparty, product_type
    return "", ""


def clean_dataframe_columns(df):
    valid_columns = [
        column
        for column in df.columns
        if (str(column) or "").strip()
        and not str(column).startswith("Unnamed:")
    ]
    return df.loc[:, valid_columns].copy()


def apply_counterparty_rules(df, rules_file):
    keyword_rules = load_rules(rules_file)
    output = clean_dataframe_columns(df)
    text_values = output.get("text", pd.Series("", index=output.index))
    matches = text_values.map(lambda text: match_text(text, keyword_rules))
    output["counterparty"] = matches.map(lambda match: match[0])
    output["product_type"] = matches.map(lambda match: match[1])
    return output


def process_file(sample_file, rules_file, output_file):
    keyword_rules = load_rules(rules_file)
    with open(sample_file, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
        fieldnames = list(rows[0]) if rows else []
        fieldnames = clean_fieldnames(rows, fieldnames)
        if "counterparty" not in fieldnames:
            fieldnames.append("counterparty")
        if "product_type" not in fieldnames:
            fieldnames.append("product_type")
    for row in rows:
        row["counterparty"], row["product_type"] = match_text(row.get("text"), keyword_rules)
    with open(output_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
