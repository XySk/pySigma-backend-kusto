import pytest

from sigma.backends.kusto import KustoBackend
from sigma.collection import SigmaCollection
from sigma.exceptions import SigmaError
from sigma.pipelines.microsoft365defender import microsoft_365_defender_pipeline


def correlation_collection(correlation: str = "") -> SigmaCollection:
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
  condition: selection
---
title: Event count
"""
        + correlation
    )


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
        correlation_collection(
            f"""correlation:
  type: event_count
  rules:
    - source-rule
  group-by:
    - User
  timespan: 5m
  condition:
    {operator}: 2
"""
        )
    )

    assert result == [
        'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"\n'
        "| summarize EventCount = count() by WindowStart = bin(TimestampField, 5m), AccountName\n"
        f"| where EventCount {query_operator} 2"
    ]


def test_event_count_correlation_requires_timestamp_option():
    backend = KustoBackend(processing_pipeline=microsoft_365_defender_pipeline())

    with pytest.raises(SigmaError, match="correlation_timestamp_field"):
        backend.convert(
            correlation_collection(
                """correlation:
  type: event_count
  rules:
    - source-rule
  group-by:
    - User
  timespan: 5m
  condition:
    gte: 2
"""
            )
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
        """correlation:
  type: event_count
  rules:
    - source-rule
  group-by:
    - User
  timespan: 5m
  condition:
    gte: 2
  generate: true
"""
    )

    assert backend.convert(collection) == [
        'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"',
        'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"\n'
        "| summarize EventCount = count() by WindowStart = bin(TimestampField, 5m), AccountName\n"
        "| where EventCount >= 2",
    ]
