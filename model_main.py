from match_counterparty import process_file
from detect_dishonours import process_file as detect_dishonours
from match_stream import assign_stream_ids

counterparty_file = "output/sample_with_counterparty_stage1.csv"
dishonours_file = "output/sample_with_counterparty_stage2.csv"
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

assign_stream_ids(
    dishonours_file,
    final_file,
)
