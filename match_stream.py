"""Assign loan stream IDs by product type and priority.

Priority is defined in ``PRODUCT_RULES``:
BNPL -> Wage Advance -> Bank -> Personal Loan -> LOC.

The final LOC stage contains a controlled refinement rule: after personal-
loan streams have been created, qualifying ``sacc-*`` streams can be merged
into one ``loc_`` stream. This refinement depends on personal-loan output
and therefore must run last.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable

import pandas as pd


# ---------------------------------------------------------------------------
# Product rule configuration
# ---------------------------------------------------------------------------

DEFAULT_GROUP_COLUMNS = ["application_id", "counterparty"]
SIMPLE_STREAM_GROUP_COLUMNS = [
    "application_id",
    "bank_account_id",
    "counterparty",
]

PERSONAL_LOAN = "personal_loan"
DISHONOUR_COLUMN = "is_dishonours"
AMOUNT_TOLERANCE = Decimal("0.05")

SACC_PREFIX = "sacc-"
SPECIAL_LOC_PREFIX = "loc_"
DEFAULT_MIN_SACC_STREAMS = 3
DEFAULT_LOC_CV_THRESHOLD = Decimal("0.2")


@dataclass(frozen=True)
class ProductRule:
    """One product-level stream matching rule."""

    priority: int
    product_type: str
    matcher: Callable[[pd.DataFrame, pd.Series, list[str]], int]


# ---------------------------------------------------------------------------
# Shared stream structures
# ---------------------------------------------------------------------------


@dataclass
class RepaymentStream:
    row_indices: list[int]
    baseline_amount: Decimal
    first_date: pd.Timestamp
    last_date: pd.Timestamp


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
        return [
            row_id
            for bucket in self.buckets
            for row_id in bucket.row_indices
        ]

    @property
    def dates_by_amount(self) -> dict[Decimal, set[pd.Timestamp]]:
        return {bucket.amount: bucket.dates for bucket in self.buckets}


class PersonalLoanStreamIdGenerator:
    """Generate the existing personal-loan stream ID formats."""

    def __init__(self) -> None:
        self._counters = {"sacc": 0, "non-sacc": 0, "unknown": 0}

    def next_for_amount(self, amount: Decimal) -> str:
        prefix = "sacc" if amount <= Decimal("2000") else "non-sacc"
        self._counters[prefix] += 1
        return f"{prefix}-{self._counters[prefix]:03d}"

    def next_unknown(self) -> str:
        self._counters["unknown"] += 1
        return f"unknown-{self._counters['unknown']:03d}"


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


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


def amount_within_tolerance(
    amount: Decimal,
    baseline: Decimal,
    tolerance: Decimal = AMOUNT_TOLERANCE,
) -> bool:
    lower_bound = baseline * (Decimal("1") - tolerance)
    upper_bound = baseline * (Decimal("1") + tolerance)
    return lower_bound <= amount <= upper_bound


def normalize_group_value(value: object) -> object:
    return "" if pd.isna(value) else value


def has_counterparty(value: object) -> bool:
    # Match the original CSV behavior: only a genuinely empty value is skipped.
    return not pd.isna(value) and str(value) != ""


def ensure_stream_id_column(df: pd.DataFrame, reset: bool = False) -> pd.DataFrame:
    output = df.copy()

    if "stream_id" not in output.columns:
        output["stream_id"] = pd.NA
    elif reset:
        output["stream_id"] = pd.NA
    else:
        output["stream_id"] = output["stream_id"].replace(r"^\s*$", pd.NA, regex=True)

    return output


# ---------------------------------------------------------------------------
# Simple grouped products: BNPL / Wage Advance / Bank / direct LOC
# ---------------------------------------------------------------------------


def assign_grouped_product_streams(
    output: pd.DataFrame,
    eligible_mask: pd.Series,
    prefix: str,
) -> int:
    """Assign one stream per application + account + counterparty combination.

    Numbering remains scoped to each application, matching the existing output:
    ``loc_001``, ``bnpl_001``, ``wage_advance_001`` and ``bank_001``.
    """

    streams_by_application: dict[object, dict[tuple[object, object, object], str]] = {}
    stream_count = 0

    for row_id, row in output.loc[eligible_mask].iterrows():
        group_values = tuple(
            normalize_group_value(row.get(column, ""))
            for column in SIMPLE_STREAM_GROUP_COLUMNS
        )
        application_id, bank_account_id, counterparty = group_values
        if not has_counterparty(counterparty):
            continue

        stream_key = (application_id, bank_account_id, counterparty)

        application_streams = streams_by_application.setdefault(
            application_id,
            {},
        )
        if stream_key not in application_streams:
            stream_count += 1
            application_streams[stream_key] = (
                f"{prefix}_{len(application_streams) + 1:03d}"
            )

        output.at[row_id, "stream_id"] = application_streams[stream_key]

    return stream_count


def identify_direct_loc_streams(
    output: pd.DataFrame,
    eligible_mask: pd.Series,
    _: list[str],
) -> int:
    """Assign streams to rows already classified as product_type == loc."""

    return assign_grouped_product_streams(output, eligible_mask, "loc")


def identify_bnpl_streams(
    output: pd.DataFrame,
    eligible_mask: pd.Series,
    _: list[str],
) -> int:
    return assign_grouped_product_streams(output, eligible_mask, "bnpl")


def identify_wage_advance_streams(
    output: pd.DataFrame,
    eligible_mask: pd.Series,
    _: list[str],
) -> int:
    return assign_grouped_product_streams(output, eligible_mask, "wage_advance")


def identify_bank_streams(
    output: pd.DataFrame,
    eligible_mask: pd.Series,
    _: list[str],
) -> int:
    return assign_grouped_product_streams(output, eligible_mask, "bank")


# ---------------------------------------------------------------------------
# Personal loan matching
# ---------------------------------------------------------------------------


def is_dishonour_credit(df: pd.DataFrame) -> pd.Series:
    return (
        df["dr_cr"].eq("credit")
        & df[DISHONOUR_COLUMN].astype("string").str.lower().eq("yes")
    )


def buckets_have_parallel_dates(
    candidate: AmountBucket,
    cluster: AmountCluster,
) -> bool:
    for amount, dates in cluster.dates_by_amount.items():
        if amount != candidate.amount and candidate.dates.intersection(dates):
            return True
    return False


def choose_amount_cluster(
    candidate: AmountBucket,
    clusters: list[AmountCluster],
) -> AmountCluster | None:
    matches: list[tuple[Decimal, AmountCluster]] = []

    for cluster in clusters:
        if not amount_within_tolerance(
            candidate.amount,
            cluster.baseline_amount,
        ):
            continue
        if buckets_have_parallel_dates(candidate, cluster):
            continue

        matches.append(
            (abs(candidate.amount - cluster.baseline_amount), cluster)
        )

    if not matches:
        return None

    matches.sort(key=lambda item: item[0])
    return matches[0][1]


def build_amount_buckets(debits: pd.DataFrame) -> list[AmountBucket]:
    buckets: list[AmountBucket] = []

    for amount, amount_rows in debits.groupby(
        "_amount_decimal",
        dropna=True,
        sort=False,
    ):
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
    debits = group[group["dr_cr"].eq("debit")].copy()
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
            clusters.append(
                AmountCluster(
                    buckets=[bucket],
                    baseline_amount=bucket.amount,
                )
            )
        else:
            cluster.buckets.append(bucket)

    repayment_streams: list[RepaymentStream] = []
    for cluster in clusters:
        cluster_rows = debits.loc[cluster.row_indices]
        repayment_streams.append(
            RepaymentStream(
                row_indices=cluster.row_indices,
                baseline_amount=cluster.baseline_amount,
                first_date=cluster_rows["_transaction_date"].min(),
                last_date=cluster_rows["_transaction_date"].max(),
            )
        )

    return repayment_streams


def build_funding_flows(group: pd.DataFrame) -> list[FundingFlow]:
    credits = group[
        group["dr_cr"].eq("credit") & ~group["_is_dishonour_credit"]
    ].copy()
    if credits.empty:
        return []

    credits["_amount_key"] = credits["amount"].map(normalize_amount_key)
    funding_flows: list[FundingFlow] = []

    for (transaction_date, _), flow_rows in credits.groupby(
        ["_transaction_date", "_amount_key"],
        dropna=False,
        sort=True,
    ):
        amount = parse_decimal_amount(flow_rows["amount"].iloc[0])
        if amount is None or pd.isna(transaction_date):
            continue

        funding_flows.append(
            FundingFlow(
                row_indices=flow_rows.index.tolist(),
                transaction_date=transaction_date,
                amount=amount,
            )
        )

    return funding_flows


def match_funding_flow(
    repayment_stream: RepaymentStream,
    funding_flows: list[FundingFlow],
) -> FundingFlow | None:
    candidates = [
        funding
        for funding in funding_flows
        if not funding.matched
        and funding.transaction_date < repayment_stream.first_date
    ]
    if not candidates:
        return None

    return max(candidates, key=lambda funding: funding.transaction_date)


def assign_dishonour_credits(
    output: pd.DataFrame,
    eligible_mask: pd.Series,
    group_columns: list[str],
) -> int:
    assigned_count = 0

    for _, group in output.loc[eligible_mask].groupby(
        group_columns,
        dropna=False,
        sort=False,
    ):
        dishonour_rows = group[group["_is_dishonour_credit"]].sort_values(
            ["_transaction_date", "_row_id"]
        )
        debit_rows = group[
            group["dr_cr"].eq("debit") & group["stream_id"].notna()
        ].copy()

        if dishonour_rows.empty or debit_rows.empty:
            continue

        debit_rows["_amount_decimal"] = debit_rows["amount"].map(
            parse_decimal_amount
        )

        for row_id, dishonour in dishonour_rows.iterrows():
            amount = parse_decimal_amount(dishonour["amount"])
            candidates = debit_rows[
                debit_rows["_transaction_date"]
                <= dishonour["_transaction_date"]
            ]

            if amount is not None:
                exact_amount_candidates = candidates[
                    candidates["_amount_decimal"].eq(amount)
                ]
                if not exact_amount_candidates.empty:
                    candidates = exact_amount_candidates
                else:
                    tolerance_candidates = candidates[
                        candidates["_amount_decimal"].map(
                            lambda debit_amount: (
                                debit_amount is not None
                                and amount_within_tolerance(
                                    amount,
                                    debit_amount,
                                )
                            )
                        )
                    ]
                    if not tolerance_candidates.empty:
                        candidates = tolerance_candidates

            if candidates.empty:
                continue

            matched = candidates.sort_values(
                ["_transaction_date", "_row_id"]
            ).iloc[-1]
            output.at[row_id, "stream_id"] = matched["stream_id"]
            assigned_count += 1

    return assigned_count


def assign_personal_loan_rule(
    output: pd.DataFrame,
    eligible_mask: pd.Series,
    group_columns: list[str],
) -> int:
    """Assign personal-loan streams to rows already claimed by this rule."""

    output["_row_id"] = output.index
    output["_transaction_date"] = pd.to_datetime(
        output["transaction_date"],
        errors="coerce",
    )
    output["_is_dishonour_credit"] = is_dishonour_credit(output)

    stream_ids = PersonalLoanStreamIdGenerator()
    stream_count = 0

    for _, group in output.loc[eligible_mask].groupby(
        group_columns,
        dropna=False,
        sort=True,
    ):
        repayment_streams = sorted(
            cluster_repayments(group),
            key=lambda stream: (
                stream.first_date,
                stream.baseline_amount,
            ),
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
            stream_count += 1

        for funding in funding_flows:
            if funding.matched:
                continue

            stream_id = stream_ids.next_for_amount(funding.amount)
            output.loc[funding.row_indices, "stream_id"] = stream_id
            stream_count += 1

    dishonour_count = assign_dishonour_credits(
        output,
        eligible_mask,
        group_columns,
    )
    output.attrs["dishonour_credit_assigned_count"] = dishonour_count

    output.drop(
        columns=["_row_id", "_transaction_date", "_is_dishonour_credit"],
        inplace=True,
    )
    return stream_count


# ---------------------------------------------------------------------------
# LOC refinement: merge qualifying SACC streams after Personal Loan
# ---------------------------------------------------------------------------


class LocStreamIdGenerator:
    """Generate ``loc_001`` IDs without colliding with existing LOC IDs."""

    def __init__(self, existing_stream_ids: pd.Series) -> None:
        self._counter = self._find_max_counter(existing_stream_ids)

    @staticmethod
    def _find_max_counter(existing_stream_ids: pd.Series) -> int:
        max_counter = 0

        for value in existing_stream_ids.dropna().astype(str):
            match = re.fullmatch(r"loc_(\d+)", value.strip().lower())
            if match:
                max_counter = max(max_counter, int(match.group(1)))

        return max_counter

    def next(self) -> str:
        self._counter += 1
        return f"{SPECIAL_LOC_PREFIX}{self._counter:03d}"


def parse_loc_amount(value: object) -> Decimal | None:
    """Return an absolute, non-zero amount for the LOC variability rule."""

    amount = parse_decimal_amount(value)
    if amount is None:
        return None

    amount = abs(amount)
    return amount if amount != 0 else None


def calculate_cv(amounts: list[Decimal]) -> Decimal | None:
    """Calculate population coefficient of variation: stddev / mean."""

    if not amounts:
        return None

    mean = sum(amounts) / Decimal(len(amounts))
    if mean == 0:
        return None

    variance = sum(
        (amount - mean) ** 2
        for amount in amounts
    ) / Decimal(len(amounts))
    return variance.sqrt() / mean


def build_sacc_funding_table(
    output: pd.DataFrame,
    group_columns: list[str],
) -> pd.DataFrame:
    """Return the earliest valid funding amount for each existing SACC stream."""

    stream_id_text = output["stream_id"].astype("string")
    eligible_mask = (
        output["product_type"].eq(PERSONAL_LOAN)
        & stream_id_text.str.lower().str.startswith(SACC_PREFIX, na=False)
        & output["dr_cr"].astype("string").str.lower().eq("credit")
        & ~output["_is_dishonour_credit"]
    )

    funding_rows = output.loc[eligible_mask].copy()
    result_columns = [*group_columns, "stream_id", "funded_amount"]

    if funding_rows.empty:
        return pd.DataFrame(columns=result_columns)

    funding_rows["_funded_amount"] = funding_rows["amount"].map(
        parse_loc_amount
    )
    funding_rows = funding_rows.dropna(
        subset=["_transaction_date", "_funded_amount"]
    )
    if funding_rows.empty:
        return pd.DataFrame(columns=result_columns)

    funding_rows = funding_rows.sort_values(
        [*group_columns, "stream_id", "_transaction_date", "_row_id"],
        kind="stable",
    )
    first_funding_rows = funding_rows.drop_duplicates(
        subset=[*group_columns, "stream_id"],
        keep="first",
    )

    return first_funding_rows[
        [*group_columns, "stream_id", "_funded_amount"]
    ].rename(columns={"_funded_amount": "funded_amount"})


def build_group_mask(
    output: pd.DataFrame,
    group_columns: list[str],
    group_key: tuple[object, ...],
) -> pd.Series:
    """Build a null-safe mask for one configured personal-loan group."""

    mask = pd.Series(True, index=output.index, dtype=bool)

    for column, value in zip(group_columns, group_key):
        if pd.isna(value):
            mask &= output[column].isna()
        else:
            mask &= output[column].eq(value)

    return mask


def merge_sacc_streams_into_loc(
    output: pd.DataFrame,
    group_columns: list[str],
    cv_threshold: Decimal = DEFAULT_LOC_CV_THRESHOLD,
    min_sacc_streams: int = DEFAULT_MIN_SACC_STREAMS,
) -> int:
    """Merge qualifying SACC streams into one special LOC stream.

    A group qualifies when it has at least ``min_sacc_streams`` SACC streams
    and the coefficient of variation of their funding amounts is greater than
    ``cv_threshold``. Only the matching ``sacc-*`` stream IDs are replaced.
    """

    if min_sacc_streams < 2:
        raise ValueError("min_sacc_streams must be at least 2.")
    if cv_threshold < 0:
        raise ValueError("cv_threshold cannot be negative.")

    output["_row_id"] = output.index
    output["_transaction_date"] = pd.to_datetime(
        output["transaction_date"],
        errors="coerce",
    )
    output["_is_dishonour_credit"] = is_dishonour_credit(output)

    funding_table = build_sacc_funding_table(output, group_columns)
    loc_id_generator = LocStreamIdGenerator(output["stream_id"])

    loc_group_count = 0
    updated_row_count = 0
    merged_sacc_stream_count = 0

    if not funding_table.empty:
        for group_key, group_funding in funding_table.groupby(
            group_columns,
            dropna=False,
            sort=True,
        ):
            if not isinstance(group_key, tuple):
                group_key = (group_key,)

            group_funding = group_funding.drop_duplicates(
                subset=["stream_id"],
                keep="first",
            )
            amounts = group_funding["funded_amount"].tolist()

            if len(amounts) < min_sacc_streams:
                continue

            cv = calculate_cv(amounts)
            if cv is None or cv <= cv_threshold:
                continue

            original_sacc_ids = set(
                group_funding["stream_id"].dropna().astype(str)
            )
            group_mask = build_group_mask(
                output,
                group_columns,
                group_key,
            )
            sacc_stream_mask = (
                output["product_type"].eq(PERSONAL_LOAN)
                & output["stream_id"].astype("string").isin(
                    original_sacc_ids
                )
            )
            update_mask = group_mask & sacc_stream_mask

            if not update_mask.any():
                continue

            output.loc[update_mask, "stream_id"] = loc_id_generator.next()

            loc_group_count += 1
            updated_row_count += int(update_mask.sum())
            merged_sacc_stream_count += len(original_sacc_ids)

    output.drop(
        columns=["_row_id", "_transaction_date", "_is_dishonour_credit"],
        inplace=True,
    )

    output.attrs["special_loc_groups_identified"] = loc_group_count
    output.attrs["loc_rows_updated"] = updated_row_count
    output.attrs["sacc_streams_merged"] = merged_sacc_stream_count
    return loc_group_count


def identify_loc_streams(
    output: pd.DataFrame,
    eligible_mask: pd.Series,
    group_columns: list[str],
) -> int:
    """Run the final LOC stage.

    1. Assign original ``product_type == loc`` rows using the existing grouped
       rule and ``loc_001`` format.
    2. Refine qualifying personal-loan ``sacc-*`` streams into ``loc_`` streams.
    """

    direct_loc_count = identify_direct_loc_streams(
        output,
        eligible_mask,
        group_columns,
    )
    special_loc_count = merge_sacc_streams_into_loc(
        output,
        group_columns,
    )

    output.attrs["direct_loc_streams_identified"] = direct_loc_count
    return direct_loc_count + special_loc_count


# ---------------------------------------------------------------------------
# Priority dispatcher
# ---------------------------------------------------------------------------


# Lower number = higher priority.
# Once a row matches one product_type, it is added to claimed_mask and cannot
# enter any later rule.
PRODUCT_RULES: tuple[ProductRule, ...] = (
    ProductRule(10, "bnpl", identify_bnpl_streams),
    ProductRule(20, "wage_advance", identify_wage_advance_streams),
    ProductRule(30, "bank", identify_bank_streams),
    ProductRule(40, PERSONAL_LOAN, assign_personal_loan_rule),
    ProductRule(50, "loc", identify_loc_streams),
)


def identify_streams(
    df: pd.DataFrame,
    group_columns: list[str] | None = None,
    reset_stream_ids: bool = True,
) -> pd.DataFrame:
    """Run all product rules in priority order."""

    group_columns = group_columns or DEFAULT_GROUP_COLUMNS
    output = ensure_stream_id_column(df, reset=reset_stream_ids)
    validate_columns(output, group_columns)

    claimed_mask = pd.Series(False, index=output.index, dtype=bool)
    stream_counts: dict[str, int] = {}

    for rule in sorted(PRODUCT_RULES, key=lambda item: item.priority):
        eligible_mask = (
            ~claimed_mask
            & output["product_type"].eq(rule.product_type)
        )

        # Claim all rows that match this product rule, including rows that
        # cannot receive a stream_id because key information is missing.
        claimed_mask |= eligible_mask

        stream_counts[rule.product_type] = rule.matcher(
            output,
            eligible_mask,
            group_columns,
        )

    output.attrs["stream_counts"] = stream_counts
    output.attrs["personal_loan_streams_identified"] = stream_counts.get(
        PERSONAL_LOAN,
        0,
    )
    return output


# ---------------------------------------------------------------------------
# Validation and model_main.py entry point
# ---------------------------------------------------------------------------


def validate_columns(df: pd.DataFrame, group_columns: list[str]) -> None:
    required_columns = {
        "product_type",
        "transaction_date",
        "amount",
        "dr_cr",
        DISHONOUR_COLUMN,
        "stream_id",
        *group_columns,
    }
    missing_columns = sorted(required_columns.difference(df.columns))
    if missing_columns:
        raise ValueError(
            f"Missing required columns: {', '.join(missing_columns)}"
        )


def assign_stream_ids(
    input_file: str,
    output_file: str,
    group_columns: list[str] | None = None,
) -> None:
    """Entry point used by model_main.py."""

    df = pd.read_csv(input_file, dtype={"stream_id": "string"})
    output = identify_streams(
        df,
        group_columns=group_columns,
        reset_stream_ids=True,
    )
    temp_file = f"{output_file}.tmp"
    output.to_csv(temp_file, index=False, encoding="utf-8")
    os.replace(temp_file, output_file)
