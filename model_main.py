from pathlib import Path
from tempfile import TemporaryDirectory

from match_counterparty import process_file
from apply_special_rules import process_file as apply_special_rules
from detect_dishonours import process_file as detect_dishonours
from loan_summary import write_loan_summary_workbook
from match_stream import assign_stream_ids

final_workbook = Path("output/sample_with_counterparty.xlsx")
legacy_csv_output = Path("output/sample_with_counterparty.csv")

with TemporaryDirectory() as temp_dir:
    counterparty_file = str(Path(temp_dir) / "sample_counterparty.csv")
    dishonours_file = str(Path(temp_dir) / "sample_dishonours.csv")
    special_rules_file = str(Path(temp_dir) / "sample_special_rules.csv")
    final_csv_file = str(Path(temp_dir) / "sample_with_counterparty.csv")

    process_file(
        "sample.csv",
        "resources/counterparty_keyword_rules.csv",
        counterparty_file,
    )

    detect_dishonours(
        counterparty_file,
        "resources/dishonours_rules.csv",
        dishonours_file,
    )

    apply_special_rules(
        dishonours_file,
        special_rules_file,
    )

    assign_stream_ids(
        special_rules_file,
        final_csv_file,
    )

    write_loan_summary_workbook(
        final_csv_file,
        final_workbook,
    )

legacy_csv_output.unlink(missing_ok=True)
