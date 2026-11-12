import pytest
import yaml

from sigma.backends.kusto import KustoBackend
from sigma.collection import SigmaCollection
from sigma.exceptions import SigmaError
from sigma.pipelines.kusto_common.schema import BaseSchema, FieldInfo, FieldMappings, TableSchema
from sigma.pipelines.microsoft365defender import microsoft_365_defender_pipeline
from sigma.processing.pipeline import ProcessingPipeline


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


def multi_rule_collection(*, source_conditions="selection", condition="gte: 2", group_by=(), aliases=""):
    collection = (
        correlation_collection(event_count_correlation(), source_conditions=source_conditions).rules[0].to_dict()
    )
    other = {
        "title": "Other source rule",
        "name": "other-rule",
        "id": "22222222-2222-2222-2222-222222222222",
        "logsource": {"category": "network_connection", "product": "windows"},
        "detection": {"selection": {"DestinationPort": 443}, "condition": "selection"},
    }
    correlation = event_count_correlation(group_by=group_by, condition=condition).replace(
        "    - source-rule", "    - source-rule\n    - other-rule"
    )
    return SigmaCollection.from_yaml(
        yaml.safe_dump(collection)
        + "---\n"
        + yaml.safe_dump(other)
        + "---\ntitle: Event count\n"
        + correlation
        + aliases
    )


def test_multi_rule_event_count_combines_source_tables_before_counting():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="Timestamp",
    )

    assert backend.convert(multi_rule_collection(group_by=("User",))) == [
        'union (DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"\n'
        '| extend _sigma_timestamp = todatetime(["Timestamp"]), _sigma_group_0 = tostring(["AccountName"])\n'
        '| extend _sigma_rule = "source-rule"),\n'
        "(DeviceNetworkEvents\n| where RemotePort == 443\n"
        '| extend _sigma_timestamp = todatetime(["Timestamp"]), _sigma_group_0 = tostring(["InitiatingProcessAccountName"])\n'
        '| extend _sigma_rule = "other-rule")\n'
        "| summarize EventCount = count() by WindowStart = bin(_sigma_timestamp, 5m), _sigma_group_0\n"
        "| where EventCount >= 2"
    ]


@pytest.mark.parametrize(
    ("invalid_state", "error"),
    [
        ("unresolved", "source rule reference is unresolved"),
        ("no-queries", "convertible source-rule filter"),
        ("no-states", "convertible source-rule filter"),
        ("unpaired", "source query and conversion state counts do not match"),
        ("no-table", "mapped query table"),
        ("different-tables", "same mapped query table"),
    ],
)
def test_multi_rule_event_count_rejects_invalid_source_conversion_state(invalid_state, error):
    collection = multi_rule_collection(source_conditions="[selection, alternate]")
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="Timestamp",
    )

    def invalidate_first_source(rule, output_format, index, condition, result):
        if rule.name == "other-rule":
            source = collection.rules[0]
            if invalid_state == "unresolved":
                collection.rules[-1].rules[0].rule = None
            elif invalid_state == "no-queries":
                source.set_conversion_result([])
            elif invalid_state == "no-states":
                source.set_conversion_states([])
            elif invalid_state == "unpaired":
                source.set_conversion_states(source.get_conversion_states()[:1])
            elif invalid_state == "no-table":
                source.get_conversion_states()[0].processing_state.pop("query_table")
            elif invalid_state == "different-tables":
                source.get_conversion_states()[1].processing_state["query_table"] = "DeviceNetworkEvents"
        return result

    with pytest.raises(SigmaError, match=error):
        backend.convert(collection, callback=invalidate_first_source)


def test_multi_rule_event_count_maps_aliases_and_timestamp_from_each_source_pipeline():
    pipeline = microsoft_365_defender_pipeline()
    pipeline.vars["kusto_field_mappings"] = FieldMappings(
        table_mappings={"DeviceProcessEvents": {"EventTime": "ProcessTime", "User": "ProcessUser"}}
    )
    backend = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="EventTime")

    def change_mapping_for_other_source(rule, output_format, index, condition, result):
        if rule.name == "other-rule":
            backend.last_processing_pipeline.vars["kusto_field_mappings"] = FieldMappings(
                table_mappings={"DeviceNetworkEvents": {"EventTime": "NetworkTime", "SourceUser": "NetworkUser"}}
            )
        return result

    collection = multi_rule_collection(
        group_by=("Actor",),
        aliases="  aliases:\n    Actor:\n      source-rule: User\n      other-rule: SourceUser\n",
    )
    (query,) = backend.convert(collection, callback=change_mapping_for_other_source)

    assert 'todatetime(["ProcessTime"]), _sigma_group_0 = tostring(["ProcessUser"])' in query
    assert 'todatetime(["NetworkTime"]), _sigma_group_0 = tostring(["NetworkUser"])' in query


def test_multi_rule_event_count_quotes_rule_identity_and_special_mapped_fields():
    collection = multi_rule_collection(group_by=("User",))
    collection.rules[-1].rules[0].reference = str(collection.rules[0].id)
    collection.rules[0].name = 'source"\\\n*'
    collection.rules[-1].rules[1].reference = str(collection.rules[1].id)
    collection.rules[1].name = None
    pipeline = microsoft_365_defender_pipeline()
    pipeline.vars["kusto_field_mappings"] = FieldMappings(
        generic_mappings={"EventTime": "Event Time", "User": "Actor's Name"}
    )
    backend = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="EventTime")

    (query,) = backend.convert(collection)

    assert 'todatetime(["Event Time"])' in query
    assert 'tostring(["Actor\'s Name"])' in query
    assert '| extend _sigma_rule = "source\\"\\\\\\n*"' in query
    assert '| extend _sigma_rule = "22222222-2222-2222-2222-222222222222"' in query


def test_multi_rule_event_count_preserves_source_columns_that_overlap_normalized_names():
    collection = multi_rule_collection(group_by=("User",))
    collection.rules[0].fields = ["_sigma_timestamp", "_sigma_timestamp_1", "_sigma_rule_1"]
    pipeline = microsoft_365_defender_pipeline()
    pipeline.vars["kusto_field_mappings"] = FieldMappings(
        generic_mappings={"EventTime": "_sigma_timestamp_2", "User": "_sigma_group_0", "Unused": "_sigma_rule"}
    )
    pipeline.vars["kusto_schema"] = BaseSchema(
        tables={"DeviceNetworkEvents": TableSchema(fields={"_sigma_rule_2": FieldInfo("string", "Original data")})}
    )
    backend = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="EventTime")

    (query,) = backend.convert(collection)

    assert (
        '| extend _sigma_timestamp_3 = todatetime(["_sigma_timestamp_2"]), _sigma_group_0_1 = tostring(["_sigma_group_0"])'
        in query
    )
    assert '| extend _sigma_rule_3 = "source-rule"' in query
    assert "bin(_sigma_timestamp_3, 5m), _sigma_group_0_1" in query
    assert "project " not in query


@pytest.mark.parametrize(
    ("operator", "query_operator"),
    [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="), ("eq", "=="), ("neq", "!=")],
)
def test_multi_rule_event_count_retains_every_search_and_duplicate_rule_matches(operator, query_operator):
    pipeline = ProcessingPipeline.from_dict(
        {"transformations": [{"type": "set_state", "key": "query_table", "val": "CustomEvents"}]}
    )
    backend = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp")
    collection = multi_rule_collection(source_conditions="[selection, alternate]", condition=f"{operator}: 3")
    collection.rules[1].detection = collection.rules[0].detection

    (query,) = backend.convert(collection)

    # The same event can satisfy both named rules, and both searches from each
    # rule must contribute rows to the one combined count.
    assert query.count('(CustomEvents\n| where CommandLine =~ "suspicious.exe"') == 2
    assert query.count('(CustomEvents\n| where Image =~ "suspicious.exe"') == 2
    assert query.count('| extend _sigma_rule = "source-rule"') == 2
    assert query.count('| extend _sigma_rule = "other-rule"') == 2
    assert query.endswith(
        "| summarize EventCount = count() by WindowStart = bin(_sigma_timestamp, 5m)\n"
        f"| where EventCount {query_operator} 3"
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
        backend.convert(correlation_collection(event_count_correlation(group_by=("User",))))


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
    collection = correlation_collection(event_count_correlation(group_by=("User",), generate=True))

    assert backend.convert(collection) == [
        'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"',
        'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"\n'
        "| summarize EventCount = count() by WindowStart = bin(TimestampField, 5m), AccountName\n"
        "| where EventCount >= 2",
    ]
