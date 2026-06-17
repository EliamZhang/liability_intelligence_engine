import csv
import os
import re

FIELD_NAME = "is_dishonours"
LEGACY_FIELD_NAME = "\u662f\u5426Dishonours"


def load_rules(rules_file):
    rules = []
    with open(rules_file, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rule_type = (row.get("rule_type") or "").strip().lower()
            pattern = row.get("pattern") or ""
            required_terms = [x.strip().lower() for x in (row.get("required_terms") or "").split(";") if x.strip()]
            if rule_type and pattern:
                rules.append((rule_type, pattern, required_terms))
    return rules


def is_dishonour(text, rules):
    text = text or ""
    lower_text = text.lower()
    for rule_type, pattern, required_terms in rules:
        if rule_type == "keyword" and pattern.lower() in lower_text:
            return "Yes"
        if rule_type == "regex" and all(term in lower_text for term in required_terms) and re.search(pattern, text):
            return "Yes"
    return "No"


def process_file(input_file, rules_file, output_file):
    rules = load_rules(rules_file)
    with open(input_file, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
        fieldnames = list(rows[0]) if rows else []
        if LEGACY_FIELD_NAME in fieldnames:
            fieldnames.remove(LEGACY_FIELD_NAME)
        if FIELD_NAME not in fieldnames:
            fieldnames.append(FIELD_NAME)
    for row in rows:
        row.pop(LEGACY_FIELD_NAME, None)
        row[FIELD_NAME] = is_dishonour(row.get("text"), rules)
    temp_file = output_file + ".tmp"
    with open(temp_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp_file, output_file)
