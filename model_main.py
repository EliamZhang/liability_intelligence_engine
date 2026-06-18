from match_counterparty import process_file
from apply_special_rules import process_file as apply_special_rules
from detect_dishonours import process_file as detect_dishonours
from match_stream import assign_stream_ids

counterparty_file = "output/sample_counterparty.csv"
dishonours_file = "output/sample_dishonours.csv"
special_rules_file = "output/sample_special_rules.csv"
final_file = "output/sample_with_counterparty.csv"


process_file(
    "sample.csv",
    "resources/counterparty_keyword_rules.csv",
    counterparty_file,
)

detect_dishonours(
    counterparty_file,
    "dishonours_rules.csv",
    dishonours_file,
)

apply_special_rules(
    dishonours_file,
    special_rules_file,
)

assign_stream_ids(
    special_rules_file,
    final_file,
)
