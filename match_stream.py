import argparse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd


DEFAULT_GROUP_COLUMNS = ["application_id", "bank_account_id", "counterparty"]
PERSONAL_LOAN = "personal_loan"
DISHONOUR_COLUMN = "is_dishonours"
AMOUNT_TOLERANCE = Decimal("0.05")
BASIC_STREAM_PREFIXES = {"bnpl": "bnpl", "wage_advance": "wage_advance", "bank": "bank", "loc": "loc"}


@dataclass
class RepaymentStream:
    row_indices: list[int]
    baseline_amount: Decimal
    first_date: pd.Timestamp


@dataclass
class FundingFlow:
    row_indices: list[int]
    transaction_date: pd.Timestamp
    amount: Decimal
    matched: bool = False


@dataclass
class AmountBucket:
    amount: Decimal
    row_indices: list[int]
    dates: set[pd.Timestamp]


@dataclass
class AmountCluster:
    buckets: list[AmountBucket]
    baseline_amount: Decimal

    @property
    def row_indices(self) -> list[int]:
        return [row_id for bucket in self.buckets for row_id in bucket.row_indices]

    @property
    def dates_by_amount(self) -> dict[Decimal, set[pd.Timestamp]]:
        return {bucket.amount: bucket.dates for bucket in self.buckets}


class StreamIdGenerator:
    def __init__(self) -> None:
        self._counters = {"sacc": 0, "non-sacc": 0, "unknown": 0}

    def next_for_amount(self, amount: Decimal) -> str:
        prefix = "sacc" if amount <= Decimal("2000") else "non-sacc"
        self._counters[prefix] += 1
        return f"{prefix}-{self._counters[prefix]:03d}"

    def next_unknown(self) -> str:
        self._counters["unknown"] += 1
        return f"unknown-{self._counters['unknown']:03d}"


def parse_decimal_amount(value: object) -> Decimal | None:
    if pd.isna(value):
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def normalize_amount_key(value: object) -> str:
    amount = parse_decimal_amount(value)
    if amount is None:
        return "" if pd.isna(value) else str(value).strip()
    return format(amount.normalize(), "f")


def amount_within_tolerance(amount: Decimal, baseline: Decimal) -> bool:
    lower_bound = baseline * (Decimal("1") - AMOUNT_TOLERANCE)
    upper_bound = baseline * (Decimal("1") + AMOUNT_TOLERANCE)
    return lower_bound <= amount <= upper_bound


def is_dishonour_credit(df: pd.DataFrame) -> pd.Series:
    return df["dr_cr"].astype("string").str.lower().eq("credit") & df[DISHONOUR_COLUMN].astype("string").str.lower().eq("yes")


def buckets_have_parallel_dates(candidate: AmountBucket, cluster: AmountCluster) -> bool:
    for amount, dates in cluster.dates_by_amount.items():
        if amount != candidate.amount and candidate.dates.intersection(dates):
            return True
    return False


def choose_amount_cluster(candidate: AmountBucket, clusters: list[AmountCluster]) -> AmountCluster | None:
    matches = []
    for cluster in clusters:
        if not amount_within_tolerance(candidate.amount, cluster.baseline_amount):
            continue
        if buckets_have_parallel_dates(candidate, cluster):
            continue
        matches.append((abs(candidate.amount - cluster.baseline_amount), cluster))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    return matches[0][1]


def build_amount_buckets(debits: pd.DataFrame) -> list[AmountBucket]:
    buckets = []
    for amount, amount_rows in debits.groupby("_amount_decimal", dropna=True, sort=False):
        buckets.append(
            AmountBucket(
                amount=amount,
                row_indices=amount_rows.index.tolist(),
                dates=set(amount_rows["_transaction_date"].dropna()),
            )
        )
    buckets.sort(key=lambda bucket: (-len(bucket.row_indices), bucket.amount))
    return buckets


def cluster_repayments(group: pd.DataFrame) -> list[RepaymentStream]:
    debits = group[group["dr_cr"].astype("string").str.lower().eq("debit")].copy()
    if debits.empty:
        return []
    debits["_amount_decimal"] = debits["amount"].map(parse_decimal_amount)
    debits = debits.dropna(subset=["_amount_decimal", "_transaction_date"])
    if debits.empty:
        return []

    clusters: list[AmountCluster] = []
    for bucket in build_amount_buckets(debits):
        cluster = choose_amount_cluster(bucket, clusters)
        if cluster is None:
            clusters.append(AmountCluster(buckets=[bucket], baseline_amount=bucket.amount))
        else:
            cluster.buckets.append(bucket)

    streams = []
    for cluster in clusters:
        cluster_rows = debits.loc[cluster.row_indices]
        streams.append(
            RepaymentStream(
                row_indices=cluster.row_indices,
                baseline_amount=cluster.baseline_amount,
                first_date=cluster_rows["_transaction_date"].min(),
            )
        )
    return streams


def build_funding_flows(group: pd.DataFrame) -> list[FundingFlow]:
    credits = group[group["dr_cr"].astype("string").str.lower().eq("credit") & ~group["_is_dishonour_credit"]].copy()
    if credits.empty:
        return []
    credits["_amount_key"] = credits["amount"].map(normalize_amount_key)

    flows = []
    for (transaction_date, _), flow_rows in credits.groupby(["_transaction_date", "_amount_key"], dropna=False, sort=True):
        amount = parse_decimal_amount(flow_rows["amount"].iloc[0])
        if amount is None or pd.isna(transaction_date):
            continue
        flows.append(
            FundingFlow(
                row_indices=flow_rows["_row_id"].tolist(),
                transaction_date=transaction_date,
                amount=amount,
            )
        )
    return flows


def match_funding_flow(repayment_stream: RepaymentStream, funding_flows: list[FundingFlow]) -> FundingFlow | None:
    candidates = [
        funding
        for funding in funding_flows
        if not funding.matched and funding.transaction_date < repayment_stream.first_date
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda funding: funding.transaction_date)


def assign_basic_stream_ids(df: pd.DataFrame) -> None:
    streams: dict[tuple[str, str], dict[tuple[str, str, str], str]] = {}
    for row_id, row in df.iterrows():
        product_type = row.get("product_type")
        if product_type not in BASIC_STREAM_PREFIXES:
            continue
        application_id = row.get("application_id", "")
        bank_account_id = row.get("bank_account_id", "")
        counterparty = row.get("counterparty", "")
        if pd.isna(counterparty) or str(counterparty).strip() == "":
            continue
        app_streams = streams.setdefault((str(product_type), str(application_id)), {})
        key = (str(application_id), str(bank_account_id), str(counterparty))
        if key not in app_streams:
            prefix = BASIC_STREAM_PREFIXES[str(product_type)]
            app_streams[key] = f"{prefix}_{len(app_streams) + 1:03d}"
        df.at[row_id, "stream_id"] = app_streams[key]


def assign_dishonour_credits(output: pd.DataFrame, eligible_mask: pd.Series, group_columns: list[str]) -> None:
    for _, group in output[eligible_mask].groupby(group_columns, dropna=False, sort=False):
        dishonour_rows = group[group["_is_dishonour_credit"]].sort_values(["_transaction_date", "_row_id"])
        debit_rows = group[
            group["dr_cr"].astype("string").str.lower().eq("debit")
            & group["stream_id"].astype("string").ne("")
        ].copy()
        if dishonour_rows.empty or debit_rows.empty:
            continue
        debit_rows["_amount_decimal"] = debit_rows["amount"].map(parse_decimal_amount)

        for row_id, dishonour in dishonour_rows.iterrows():
            amount = parse_decimal_amount(dishonour["amount"])
            candidates = debit_rows[debit_rows["_transaction_date"] <= dishonour["_transaction_date"]]
            if amount is not None:
                exact_amount_candidates = candidates[candidates["_amount_decimal"].eq(amount)]
                if not exact_amount_candidates.empty:
                    candidates = exact_amount_candidates
                else:
                    tolerance_candidates = candidates[
                        candidates["_amount_decimal"].map(
                            lambda debit_amount: debit_amount is not None and amount_within_tolerance(amount, debit_amount)
                        )
                    ]
                    if not tolerance_candidates.empty:
                        candidates = tolerance_candidates
            if candidates.empty:
                continue
            matched = candidates.sort_values(["_transaction_date", "_row_id"]).iloc[-1]
            output.at[row_id, "stream_id"] = matched["stream_id"]


def identify_personal_loan_streams(df: pd.DataFrame, group_columns: list[str]) -> pd.DataFrame:
    output = df.copy()
    output["_row_id"] = output.index
    output["_transaction_date"] = pd.to_datetime(output["transaction_date"], errors="coerce")
    output["_is_dishonour_credit"] = is_dishonour_credit(output)

    stream_ids = StreamIdGenerator()
    eligible_mask = output["product_type"].astype("string").eq(PERSONAL_LOAN)

    for _, group in output[eligible_mask].groupby(group_columns, dropna=False, sort=True):
        repayment_streams = sorted(
            cluster_repayments(group),
            key=lambda stream: (stream.first_date, stream.baseline_amount),
        )
        funding_flows = build_funding_flows(group)

        for repayment_stream in repayment_streams:
            funding = match_funding_flow(repayment_stream, funding_flows)
            if funding is None:
                stream_id = stream_ids.next_unknown()
            else:
                funding.matched = True
                stream_id = stream_ids.next_for_amount(funding.amount)
                output.loc[funding.row_indices, "stream_id"] = stream_id

            output.loc[repayment_stream.row_indices, "stream_id"] = stream_id

        for funding in funding_flows:
            if funding.matched:
                continue
            stream_id = stream_ids.next_for_amount(funding.amount)
            output.loc[funding.row_indices, "stream_id"] = stream_id

    assign_dishonour_credits(output, eligible_mask, group_columns)
    output = output.drop(columns=["_row_id", "_transaction_date", "_is_dishonour_credit"], errors="ignore")
    return output


def validate_columns(df: pd.DataFrame, group_columns: list[str]) -> None:
    required_columns = {"product_type", "transaction_date", "amount", "dr_cr", DISHONOUR_COLUMN, *group_columns}
    missing_columns = sorted(required_columns.difference(df.columns))
    if missing_columns:
        raise ValueError(f"Missing required columns: {', '.join(missing_columns)}")


def assign_stream_ids(input_file: str, output_file: str) -> None:
    df = pd.read_csv(input_file, encoding="utf-8-sig")
    if "stream_id" not in df.columns:
        df["stream_id"] = ""
    validate_columns(df, DEFAULT_GROUP_COLUMNS)

    assign_basic_stream_ids(df)
    output = identify_personal_loan_streams(df, DEFAULT_GROUP_COLUMNS)

    output.to_csv(output_file, index=False, encoding="utf-8-sig")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assign stream_id values for supported liability product types.")
    parser.add_argument("-i", "--input", default="output/sample_special_rules.csv", help="Input CSV path.")
    parser.add_argument("-o", "--output", default="output/sample_with_counterparty.csv", help="Output CSV path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    assign_stream_ids(str(Path(args.input)), str(Path(args.output)))


if __name__ == "__main__":
    main()
