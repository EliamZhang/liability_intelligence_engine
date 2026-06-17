import csv
import re


def load_rules(rules_file):
    pattern = re.compile(r"\b\d+\s+of\s+\d+\b")
    keyword_rules, structure_rules = [], []
    with open(rules_file, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            counterparty = row.get("counterparty")
            product_type = row.get("product_type", "")
            match_type = (row.get("match_type") or "keyword").strip().lower()
            if not counterparty:
                continue
            if match_type == "keyword" and row.get("keyword"):
                for keyword in row["keyword"].split(";"):
                    keyword = keyword.strip().lower()
                    if keyword:
                        keyword_rules.append((keyword, counterparty, product_type))
            elif match_type == "structure":
                structure_rules.append((counterparty, product_type))
    return pattern, keyword_rules, structure_rules


def match_text(text, pattern, keyword_rules, structure_rules):
    text = (text or "").lower()
    for keyword, counterparty, product_type in keyword_rules:
        if keyword in text:
            return counterparty, product_type
    for counterparty, product_type in structure_rules:
        if pattern.search(text):
            return counterparty, product_type
    return "", ""


def process_file(sample_file, rules_file, output_file):
    pattern, keyword_rules, structure_rules = load_rules(rules_file)
    with open(sample_file, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
        fieldnames = list(rows[0]) if rows else []
        if "counterparty" not in fieldnames:
            fieldnames.append("counterparty")
        if "product_type" not in fieldnames:
            fieldnames.append("product_type")
    for row in rows:
        row["counterparty"], row["product_type"] = match_text(row.get("text"), pattern, keyword_rules, structure_rules)
    with open(output_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
