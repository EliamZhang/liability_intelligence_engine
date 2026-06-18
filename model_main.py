from pathlib import Path

import pandas as pd

from apply_special_rules import apply_special_rules
from detect_dishonours import apply_dishonour_rules
from loan_summary import write_loan_summary_workbook_from_dataframe
from match_counterparty import apply_counterparty_rules
from match_stream import add_final_product_type, identify_streams


FINAL_WORKBOOK = Path("output/sample_with_counterparty.xlsx")
LEGACY_CSV_OUTPUT = Path("output/sample_with_counterparty.csv")


def build_transactions() -> pd.DataFrame:
    transactions = pd.read_csv("sample.csv", encoding="utf-8-sig")
    transactions = apply_counterparty_rules(
        transactions,
        "resources/counterparty_keyword_rules.csv",
    )
    transactions = apply_dishonour_rules(
        transactions,
        "resources/dishonours_rules.csv",
    )
    transactions = apply_special_rules(transactions)
    transactions = identify_streams(transactions, reset_stream_ids=True)
    return add_final_product_type(transactions)


def main() -> None:
    transactions = build_transactions()
    write_loan_summary_workbook_from_dataframe(
        transactions,
        FINAL_WORKBOOK,
    )
    LEGACY_CSV_OUTPUT.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
