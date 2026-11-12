import pytest

from sigma.backends.kusto import KustoBackend
from sigma.collection import SigmaCollection
from sigma.exceptions import SigmaError
from sigma.pipelines.microsoft365defender import microsoft_365_defender_pipeline


def correlation_collection(correlation: str = "", source_conditions: str = "selection") -> SigmaCollection:
    return SigmaCollection.from_yaml(
        """
title: Source rule
name: source-rule
id: 11111111-1111-1111-1111-111111111111
logsource:
  category: process_creation
  product: windows
detection:
  selection:
    CommandLine: suspicious.exe
  alternate:
    Image: suspicious.exe
  condition: """
        + source_conditions
        + """
---
title: Event count
"""
        + correlation
    )


def event_count_correlation(*, group_by=(), condition: str = "gte: 2", generate: bool = False) -> str:
    lines = ["correlation:", "  type: event_count", "  rules:", "    - source-rule"]
    if group_by:
        lines.extend(["  group-by:", *(f"    - {field}" for field in group_by)])
    lines.extend(["  timespan: 5m", "  condition:", f"    {condition}"])
    if generate:
        lines.append("  generate: true")
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    ("operator", "query_operator"),
    [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="), ("eq", "=="), ("neq", "!=")],
)
def test_event_count_correlation_uses_source_pipeline_and_fixed_window(operator, query_operator):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="TimestampField",
    )

    result = backend.convert(
        correlation_collection(event_count_correlation(group_by=("User",), condition=f"{operator}: 2"))
    )

    assert result == [
        'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"\n'
        "| summarize EventCount = count() by WindowStart = bin(TimestampField, 5m), AccountName\n"
        f"| where EventCount {query_operator} 2"
    ]


def test_event_count_correlation_preserves_multiple_source_queries():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="TimestampField",
    )

    result = backend.convert(
        correlation_collection(
            event_count_correlation(),
            source_conditions="[selection, alternate]",
        )
    )

    assert result == [
        'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"\n'
        "| summarize EventCount = count() by WindowStart = bin(TimestampField, 5m)\n"
        "| where EventCount >= 2",
        'DeviceProcessEvents\n| where FolderPath =~ "suspicious.exe"\n'
        "| summarize EventCount = count() by WindowStart = bin(TimestampField, 5m)\n"
        "| where EventCount >= 2",
    ]


def test_event_count_correlation_rejects_unpaired_source_query_states():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="TimestampField",
    )

    def skip_second_query(rule, output_format, index, condition, result):
        return None if index == 1 else result

    with pytest.raises(SigmaError, match="source query and conversion state counts do not match"):
        backend.convert(
            correlation_collection(
                event_count_correlation(),
                source_conditions="[selection, alternate]",
            ),
            callback=skip_second_query,
        )


def test_event_count_correlation_requires_timestamp_option():
    backend = KustoBackend(processing_pipeline=microsoft_365_defender_pipeline())

    with pytest.raises(SigmaError, match="correlation_timestamp_field"):
        backend.convert(
            correlation_collection(event_count_correlation(group_by=("User",)))
        )


@pytest.mark.parametrize(
    "correlation",
    [
        """correlation:
  type: temporal
  rules:
    - source-rule
  timespan: 5m
""",
        """correlation:
  type: event_count
  rules:
    - source-rule
    - other-rule
  timespan: 5m
  condition:
    gte: 2
""",
        """correlation:
  type: event_count
  rules:
    - source-rule
  group-by:
    - User
  aliases:
    User: UserName
  timespan: 5m
  condition:
    gte: 2
""",
        """correlation:
  type: event_count
  rules:
    - source-rule
  timespan: 5m
  condition:
    - gte: 2
    - lte: 5
""",
    ],
)
def test_unsupported_correlation_inputs_fail_clearly(correlation):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="TimestampField",
    )

    with pytest.raises(SigmaError):
        backend.convert(correlation_collection(correlation))


def test_event_count_range_condition_error_explains_single_operator_limit():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="TimestampField",
    )
    with pytest.raises(SigmaError, match="exactly one condition item"):
        backend.convert(
            correlation_collection(
                """correlation:
  type: event_count
  rules:
    - source-rule
  timespan: 5m
  condition:
    gte: 2
    lte: 5
"""
            )
        )


def test_generate_controls_standalone_source_query():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="TimestampField",
    )
    collection = correlation_collection(
        event_count_correlation(group_by=("User",), generate=True)
    )

    assert backend.convert(collection) == [
        'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"',
        'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"\n'
        "| summarize EventCount = count() by WindowStart = bin(TimestampField, 5m), AccountName\n"
        "| where EventCount >= 2",
    ]
