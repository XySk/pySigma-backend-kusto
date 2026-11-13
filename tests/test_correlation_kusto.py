import runpy
from pathlib import Path

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


def test_single_source_event_count_combines_searches_and_maps_aliases():
    correlation = event_count_correlation(group_by=("Actor",)) + ("  aliases:\n    Actor:\n      source-rule: User\n")
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="Timestamp",
    )

    (query,) = backend.convert(correlation_collection(correlation, source_conditions="[selection, alternate]"))

    assert query.count("(DeviceProcessEvents\n| where ") == 2
    assert query.count('_sigma_group_0 = tostring(["AccountName"])') == 2
    assert query.count('| extend _sigma_rule = "source-rule"') == 2
    assert query.count("| summarize EventCount") == 1


def test_multi_rule_event_count_combines_source_tables_before_counting():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="Timestamp",
    )

    (query,) = backend.convert(multi_rule_collection(group_by=("User",)))

    assert query.startswith(
        'let _sigma_weights = union (DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"\n'
        '| extend _sigma_timestamp = todatetime(["Timestamp"]), _sigma_group_0 = tostring(["AccountName"])\n'
        '| extend _sigma_rule = "source-rule"),\n'
        "(DeviceNetworkEvents\n| where RemotePort == 443\n"
        '| extend _sigma_timestamp = todatetime(["Timestamp"]), _sigma_group_0 = tostring(["InitiatingProcessAccountName"])\n'
        '| extend _sigma_rule = "other-rule")\n'
    )
    assert "| summarize EventCount = sum(_sigma_weight) by _sigma_window_end, _sigma_group_0\n" in query
    assert query.endswith('["User"] = _sigma_group_0, EventCount')


def test_event_count_uses_exact_inclusive_windows_at_distinct_event_timestamps():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="Timestamp",
    )

    (query,) = backend.convert(correlation_collection(event_count_correlation(group_by=("User", "DeviceName"))))

    assert query == (
        'let _sigma_weights = union (DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"\n'
        '| extend _sigma_timestamp = todatetime(["Timestamp"]), _sigma_group_0 = tostring(["AccountName"]), '
        '_sigma_group_1 = tostring(["DeviceName"])\n'
        '| extend _sigma_rule = "source-rule")\n'
        "| where isnotnull(_sigma_timestamp) and isnotempty(_sigma_group_0) and isnotempty(_sigma_group_1)\n"
        "| summarize _sigma_weight = count() by _sigma_timestamp, _sigma_group_0, _sigma_group_1;\n"
        "_sigma_weights\n"
        "| project _sigma_group_0, _sigma_group_1, _sigma_window_end = _sigma_timestamp, "
        "_sigma_bin = bin(_sigma_timestamp, 5m)\n"
        "| join kind=inner (\n"
        "    _sigma_weights\n"
        "    | extend _sigma_bin = pack_array(bin(_sigma_timestamp, 5m), bin(_sigma_timestamp, 5m) + 5m)\n"
        "    | mv-expand _sigma_bin to typeof(datetime)\n"
        ") on _sigma_group_0, _sigma_group_1, _sigma_bin\n"
        "| where _sigma_timestamp between ((_sigma_window_end - 5m) .. _sigma_window_end)\n"
        "| summarize EventCount = sum(_sigma_weight) by _sigma_window_end, _sigma_group_0, _sigma_group_1\n"
        "| where EventCount >= 2\n"
        "| project WindowStart = _sigma_window_end - 5m, WindowEnd = _sigma_window_end, "
        '["User"] = _sigma_group_0, ["DeviceName"] = _sigma_group_1, EventCount'
    )


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
    assert "_sigma_window_end = _sigma_timestamp_3, _sigma_bin = bin(_sigma_timestamp_3, 5m)" in query
    assert "| summarize EventCount = sum(_sigma_weight) by _sigma_window_end, _sigma_group_0_1" in query
    assert "| project " not in query.split("| summarize _sigma_weight")[0]


def test_event_count_preserves_columns_that_overlap_sliding_window_names():
    collection = correlation_collection(event_count_correlation(group_by=("User",)))
    collection.rules[0].fields = ["_sigma_weight", "_sigma_window_end", "_sigma_bin", "_sigma_weights"]
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(collection)

    assert query.startswith("let _sigma_weights_1 = union (")
    assert "| summarize _sigma_weight_1 = count() by _sigma_timestamp, _sigma_group_0;" in query
    assert "_sigma_window_end_1 = _sigma_timestamp, _sigma_bin_1 = bin(_sigma_timestamp, 5m)" in query
    assert "| summarize EventCount = sum(_sigma_weight_1) by _sigma_window_end_1, _sigma_group_0" in query


def test_event_count_quotes_requested_group_names_in_result_summary():
    correlation = event_count_correlation(group_by=("'Actor\" Name'",)) + (
        '  aliases:\n    Actor" Name:\n      source-rule: User\n'
    )
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(correlation_collection(correlation))

    assert '_sigma_group_0 = tostring(["AccountName"])' in query
    assert query.endswith('["Actor\\" Name"] = _sigma_group_0, EventCount')


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
    assert "| summarize _sigma_weight = count() by _sigma_timestamp;" in query
    assert "| summarize EventCount = sum(_sigma_weight) by _sigma_window_end\n" in query
    assert f"| where EventCount {query_operator} 3\n" in query


@pytest.mark.parametrize(
    ("operator", "query_operator"),
    [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="), ("eq", "=="), ("neq", "!=")],
)
def test_event_count_correlation_uses_source_pipeline_and_comparison(operator, query_operator):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="TimestampField",
    )

    result = backend.convert(
        correlation_collection(event_count_correlation(group_by=("User",), condition=f"{operator}: 2"))
    )

    (query,) = result
    assert '(DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"\n' in query
    assert '_sigma_timestamp = todatetime(["TimestampField"]), _sigma_group_0 = tostring(["AccountName"])' in query
    assert f"| where EventCount {query_operator} 2\n" in query


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

    (query,) = result
    assert '(DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"\n' in query
    assert '(DeviceProcessEvents\n| where FolderPath =~ "suspicious.exe"\n' in query
    assert query.count('| extend _sigma_rule = "source-rule"') == 2
    assert query.count("| summarize EventCount") == 1


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


@pytest.mark.parametrize("span", ["0m", "-5m"])
def test_event_count_requires_a_positive_timespan(span):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )
    correlation = event_count_correlation().replace("5m", span)

    with pytest.raises(SigmaError, match="positive timespan"):
        backend.convert(correlation_collection(correlation))


@pytest.mark.parametrize(("span", "seconds"), [("2w", 1209600), ("1M", 2629746), ("1y", 31556952)])
def test_event_count_renders_extended_sigma_duration_units_as_kusto_seconds(span, seconds):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )
    correlation = event_count_correlation().replace("5m", span)

    (query,) = backend.convert(correlation_collection(correlation))

    assert f"bin(_sigma_timestamp, {seconds}s)" in query
    assert f"between ((_sigma_window_end - {seconds}s) .. _sigma_window_end)" in query
    assert f"WindowStart = _sigma_window_end - {seconds}s" in query


@pytest.mark.parametrize("group", ["WindowStart", "WindowEnd", "EventCount"])
def test_event_count_rejects_group_names_that_collide_with_result_metrics(group):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    with pytest.raises(SigmaError, match="grouping fields conflict with result columns"):
        backend.convert(correlation_collection(event_count_correlation(group_by=(group,))))


def test_event_count_rejects_chained_sources_until_chaining_is_supported():
    child = event_count_correlation().replace("correlation:", "name: child-correlation\ncorrelation:")
    parent = event_count_correlation().replace("source-rule", "child-correlation")
    collection = SigmaCollection.from_yaml(
        "---\n".join(
            [
                yaml.safe_dump(correlation_collection(event_count_correlation()).rules[0].to_dict()),
                "title: Child\n" + child,
                "title: Parent\n" + parent,
            ]
        )
    )
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    with pytest.raises(SigmaError, match="Chained correlation sources are not yet supported"):
        backend.convert(collection)


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

    standalone, correlation = backend.convert(collection)
    assert standalone == 'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"'
    assert '(DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"\n' in correlation
    assert "| where EventCount >= 2\n" in correlation


def test_prepared_kql_fixtures_use_current_public_conversion_output():
    fixture_directory = Path(__file__).parent / "fixtures" / "correlation"
    prepared = runpy.run_path(str(fixture_directory / "generate.py"))["fixtures"]()

    for filename, content in prepared.items():
        assert (fixture_directory / filename).read_text() == content
