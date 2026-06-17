from match_counterparty import process_file
from match_stream import assign_stream_ids


process_file(
    "sample.csv",
    "resources/counterparty_keyword_rules.csv",
    "output/sample_with_counterparty.csv",
)

assign_stream_ids(
    "output/sample_with_counterparty.csv",
    "output/sample_with_counterparty.csv",
)
