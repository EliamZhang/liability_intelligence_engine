from match_counterparty import process_file


process_file(
    "sample.csv",
    "resources/counterparty_keyword_rules.csv",
    "output/sample_with_counterparty.csv",
)
