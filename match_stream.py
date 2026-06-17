import csv
import os


def assign_stream_ids(input_file, output_file):
    with open(input_file, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
        fieldnames = list(rows[0]) if rows else []
        if "stream_id" not in fieldnames:
            fieldnames.append("stream_id")

    stream_prefixes = {"bnpl": "bnpl", "wage_advance": "wage_advance", "bank": "bank"}
    streams = {}
    for row in rows:
        row["stream_id"] = ""
        product_type = row.get("product_type")
        if product_type not in stream_prefixes:
            continue
        application_id = row.get("application_id", "")
        bank_account_id = row.get("bank_account_id", "")
        counterparty = row.get("counterparty", "")
        if not counterparty:
            continue
        app_streams = streams.setdefault((product_type, application_id), {})
        key = (application_id, bank_account_id, counterparty)
        if key not in app_streams:
            prefix = stream_prefixes[product_type]
            app_streams[key] = f"{prefix}_{len(app_streams) + 1:03d}"
        row["stream_id"] = app_streams[key]

    temp_file = output_file + ".tmp"
    with open(temp_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp_file, output_file)
