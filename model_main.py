import csv

with open("resources/counterparty_keyword_rules.csv", encoding="utf-8-sig", newline="") as f:
    rules = []
    for row in csv.DictReader(f):
        if row.get("keyword") and row.get("counterparty"):
            for keyword in row["keyword"].split(";"):
                keyword = keyword.strip().lower()
                if keyword:
                    rules.append((keyword, row["counterparty"]))

with open("sample.csv", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
    fieldnames = list(rows[0]) if rows else []
    if "counterparty" not in fieldnames:
        fieldnames.append("counterparty")

for row in rows:
    text = (row.get("text") or "").lower()
    row["counterparty"] = next((cp for kw, cp in rules if kw in text), "")

with open("output/sample_with_counterparty.csv", "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
