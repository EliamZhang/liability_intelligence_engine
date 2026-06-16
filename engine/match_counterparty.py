import csv

sample_file = "sample.csv"
rules_file = "resources/counterparty_keyword_rules.csv"
output_file = "output/sample_with_counterparty.csv"

with open(rules_file, encoding="utf-8-sig", newline="") as f:
    rules = []
    for row in csv.DictReader(f):
        if row.get("keyword") and row.get("counterparty"):
            for keyword in row["keyword"].split(";"):
                keyword = keyword.strip()
                if keyword:
                    rules.append((keyword.lower(), row["counterparty"]))

with open(sample_file, encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
    fieldnames = list(rows[0]) if rows else []
    if "counterparty" not in fieldnames:
        fieldnames.append("counterparty")

for row in rows:
    text = row.get("text") or ""
    text = text.lower()
    row["counterparty"] = next((cp for kw, cp in rules if kw in text), "")

with open(output_file, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
