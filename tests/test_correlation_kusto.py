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


def multi_rule_collection(
    *,
    source_conditions="selection",
    condition="gte: 2",
    group_by=(),
    aliases="",
    counted_field=None,
    other_source_conditions="selection",
):
    collection = (
        correlation_collection(event_count_correlation(), source_conditions=source_conditions).rules[0].to_dict()
    )
    other = {
        "title": "Other source rule",
        "name": "other-rule",
        "id": "22222222-2222-2222-2222-222222222222",
        "logsource": {"category": "network_connection", "product": "windows"},
        "detection": {"selection": {"DestinationPort": 443}, "condition": other_source_conditions},
    }
    correlation = (
        event_count_correlation(group_by=group_by, condition=condition)
        if counted_field is None
        else value_count_correlation(field=counted_field, group_by=group_by, condition=condition)
    ).replace("    - source-rule", "    - source-rule\n    - other-rule")
    return SigmaCollection.from_yaml(
        yaml.safe_dump(collection)
        + "---\n"
        + yaml.safe_dump(other)
        + "---\ntitle: Event count\n"
        + correlation
        + aliases
    )


def value_count_correlation(*, field="User", group_by=(), condition="gte: 2", generate=False):
    return (
        event_count_correlation(group_by=group_by, condition=condition, generate=generate)
        .replace("type: event_count", "type: value_count")
        .replace("  condition:\n", f"  condition:\n    field: {field}\n")
    )


def temporal_correlation(*, group_by=(), condition=None, generate=False):
    correlation = event_count_correlation(group_by=group_by, condition=condition or "gte: 1", generate=generate)
    correlation = correlation.replace("type: event_count", "type: temporal")
    return correlation if condition is not None else correlation.replace("  condition:\n    gte: 1\n", "")


def temporal_collection(*, condition=None, third_source=True, **kwargs):
    documents = [rule.to_dict() for rule in multi_rule_collection(**kwargs).rules]
    correlation = documents.pop()
    correlation["correlation"]["type"] = "temporal"
    correlation["correlation"].pop("condition")
    if condition is not None:
        correlation["correlation"]["condition"] = condition
    if third_source:
        documents.append(
            {
                **documents[0],
                "title": "Third source rule",
                "name": "third-rule",
                "id": "33333333-3333-3333-3333-333333333333",
            }
        )
        correlation["correlation"]["rules"].append("third-rule")
    return SigmaCollection.from_yaml(yaml.safe_dump_all([*documents, correlation]))


def test_temporal_defaults_to_all_source_types_in_exact_event_driven_windows():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(temporal_collection())

    assert '| extend _sigma_rule = "source-rule"' in query
    assert '| extend _sigma_rule = "other-rule"' in query
    assert '| extend _sigma_rule = "third-rule"' in query
    assert "| where isnotnull(_sigma_timestamp)\n| summarize by _sigma_timestamp, _sigma_rule;\n" in query
    assert (
        "| project _sigma_window_end = _sigma_timestamp, _sigma_bin = bin(_sigma_timestamp, 5m)\n"
        "| distinct *\n| join kind=inner (\n"
    ) in query
    assert "| where _sigma_timestamp between ((_sigma_window_end - 5m) .. _sigma_window_end)\n" in query
    assert (
        "| summarize RuleCount = count_distinctif(_sigma_rule, isnotempty(_sigma_rule)) by _sigma_window_end\n" in query
    )
    assert "| where RuleCount >= 3\n" in query
    assert query.endswith("| project WindowStart = _sigma_window_end - 5m, WindowEnd = _sigma_window_end, RuleCount")


@pytest.mark.parametrize(
    ("operator", "query_operator"),
    [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="), ("eq", "=="), ("neq", "!=")],
)
def test_temporal_explicit_conditions_compare_distinct_types_without_requiring_all(operator, query_operator):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(temporal_collection(condition={operator: 2}))

    assert f"| where RuleCount {query_operator} 2\n" in query
    assert "RuleCount >= 3" not in query
    assert "RuleCount = count_distinctif(_sigma_rule, isnotempty(_sigma_rule))" in query
    assert "make_set(" not in query
    assert "dcount(" not in query


@pytest.mark.parametrize(("condition", "threshold"), [(None, 1), ({"gte": 2}, 2)])
def test_temporal_repeated_references_are_one_type_but_do_not_change_explicit_thresholds(condition, threshold):
    correlation = temporal_correlation(condition="gte: 2" if condition else None).replace(
        "    - source-rule", "    - source-rule\n    - 11111111-1111-1111-1111-111111111111"
    )
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(correlation_collection(correlation))

    assert query.count('| extend _sigma_rule = "source-rule"') == 2
    assert f"| where RuleCount >= {threshold}\n" in query


@pytest.mark.parametrize(
    ("correlation_rule", "metric"),
    [
        (event_count_correlation, "EventCount"),
        (value_count_correlation, "ValueCount"),
        (temporal_correlation, "RuleCount"),
    ],
)
def test_correlation_aliases_resolve_rule_identity_across_name_and_id_references(correlation_rule, metric):
    kwargs = {"field": "Actor"} if correlation_rule is value_count_correlation else {}
    correlation = correlation_rule(group_by=("Actor",), **kwargs).replace(
        "    - source-rule", "    - 11111111-1111-1111-1111-111111111111"
    )
    correlation += "  aliases:\n    Actor:\n      source-rule: User\n"
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(correlation_collection(correlation))

    assert '_sigma_group_0 = tostring(["AccountName"])' in query
    assert query.endswith(f'["Actor"] = _sigma_group_0, {metric}')
    if correlation_rule is value_count_correlation:
        assert '_sigma_value = tostring(["AccountName"])' in query


def test_temporal_maps_group_aliases_and_timestamps_for_all_searches_across_tables():
    collection = temporal_collection(
        third_source=False,
        source_conditions="[selection, alternate]",
        other_source_conditions=["selection", "selection"],
        group_by=("Host", "User"),
        aliases="  aliases:\n    Host:\n      source-rule: Computer\n      other-rule: SourceComputer\n",
    )
    pipeline = microsoft_365_defender_pipeline()
    pipeline.vars["kusto_field_mappings"] = FieldMappings(
        table_mappings={
            "DeviceProcessEvents": {"EventTime": "ProcessTime", "Computer": "ProcessHost", "User": "ProcessUser"},
            "DeviceNetworkEvents": {"EventTime": "NetworkTime", "SourceComputer": "NetworkHost", "User": "NetworkUser"},
        }
    )

    (query,) = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="EventTime").convert(collection)

    assert query.count("(DeviceProcessEvents\n| where ") == 2
    assert query.count("(DeviceNetworkEvents\n| where ") == 2
    assert (
        query.count(
            'todatetime(["ProcessTime"]), _sigma_group_0 = tostring(["ProcessHost"]), _sigma_group_1 = tostring(["ProcessUser"])'
        )
        == 2
    )
    assert (
        query.count(
            'todatetime(["NetworkTime"]), _sigma_group_0 = tostring(["NetworkHost"]), _sigma_group_1 = tostring(["NetworkUser"])'
        )
        == 2
    )
    assert (
        "| where isnotnull(_sigma_timestamp) and isnotempty(_sigma_group_0) and isnotempty(_sigma_group_1)\n" in query
    )
    assert "| summarize by _sigma_timestamp, _sigma_group_0, _sigma_group_1, _sigma_rule;" in query
    assert ") on _sigma_group_0, _sigma_group_1, _sigma_bin\n" in query
    assert (
        "| summarize RuleCount = count_distinctif(_sigma_rule, isnotempty(_sigma_rule)) by _sigma_window_end, _sigma_group_0, _sigma_group_1\n"
        in query
    )
    assert query.endswith('["Host"] = _sigma_group_0, ["User"] = _sigma_group_1, RuleCount')


@pytest.mark.parametrize("named", [True, False])
def test_temporal_preserves_colliding_source_columns_escaped_identity_and_standalone_searches(named):
    collection = correlation_collection(temporal_correlation(generate=True), source_conditions="[selection, alternate]")
    documents = [rule.to_dict() for rule in collection.rules]
    source = documents[0]
    source["fields"] = ["_sigma_timestamp", "_sigma_rule", "_sigma_rule_1", "_sigma_types"]
    if named:
        source["name"] = 'source"rule'
        identity = 'source\\"rule'
    else:
        source.pop("name")
        identity = source["id"]
    documents[-1]["correlation"]["rules"] = [source["id"]]
    documents[-1]["correlation"]["generate"] = True
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    *standalone, query = backend.convert(SigmaCollection.from_yaml(yaml.safe_dump_all(documents)))

    assert len(standalone) == 2
    assert standalone[0] == 'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"'
    assert query.startswith("let _sigma_types_1 = union ")
    assert query.count('_sigma_timestamp_1 = todatetime(["Timestamp"])') == 2
    assert query.count(f'| extend _sigma_rule_2 = "{identity}"') == 2
    assert "| summarize by _sigma_timestamp_1, _sigma_rule_2;" in query
    assert "RuleCount = count_distinctif(_sigma_rule_2, isnotempty(_sigma_rule_2))" in query


@pytest.mark.parametrize("group", ["WindowStart", "WindowEnd", "RuleCount"])
def test_temporal_rejects_group_names_that_collide_with_results(group):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    with pytest.raises(SigmaError, match="grouping fields conflict with result columns"):
        backend.convert(correlation_collection(temporal_correlation(group_by=(group,))))


def test_ordered_temporal_remains_clearly_unsupported():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    with pytest.raises(SigmaError, match="Ordered temporal correlations are not yet supported"):
        backend.convert(
            correlation_collection(temporal_correlation().replace("type: temporal", "type: temporal_ordered"))
        )


def test_value_count_retains_values_for_exact_window_wide_counts_and_missing_value_anchors():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(correlation_collection(value_count_correlation(condition="eq: 0")))

    assert '_sigma_value = tostring(["AccountName"])' in query
    assert "| where isnotnull(_sigma_timestamp)\n| summarize by _sigma_timestamp, _sigma_value;" in query
    assert (
        "| project _sigma_window_end = _sigma_timestamp, _sigma_bin = bin(_sigma_timestamp, 5m)\n| distinct *\n"
        in query
    )
    assert "| join kind=inner (\n    _sigma_values\n" in query
    assert "| where _sigma_timestamp between ((_sigma_window_end - 5m) .. _sigma_window_end)\n" in query
    assert (
        "| summarize ValueCount = count_distinctif(_sigma_value, isnotempty(_sigma_value)) by _sigma_window_end\n"
        in query
    )
    assert "| where ValueCount == 0\n" in query
    assert query.endswith("| project WindowStart = _sigma_window_end - 5m, WindowEnd = _sigma_window_end, ValueCount")


@pytest.mark.parametrize(
    ("operator", "query_operator", "threshold"),
    [("gt", ">", 2), ("gte", ">=", 2), ("lt", "<", 2), ("lte", "<=", 2), ("eq", "==", 0), ("neq", "!=", 2)],
)
def test_multi_field_value_count_requires_each_exact_count_and_retains_missing_value_anchors(
    operator, query_operator, threshold
):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(
        correlation_collection(
            value_count_correlation(
                field="[User, Target]", group_by=("DeviceName",), condition=f"{operator}: {threshold}"
            )
        )
    )

    assert '_sigma_value_0 = tostring(["AccountName"]), _sigma_value_1 = tostring(["Target"])' in query
    assert (
        "| where isnotnull(_sigma_timestamp) and isnotempty(_sigma_group_0)\n"
        "| summarize by _sigma_timestamp, _sigma_group_0, _sigma_value_0, _sigma_value_1;\n"
    ) in query
    assert "| distinct *\n| join kind=inner (" in query
    assert "| where _sigma_timestamp between ((_sigma_window_end - 5m) .. _sigma_window_end)\n" in query
    assert (
        '| summarize ["ValueCount_User"] = count_distinctif(_sigma_value_0, isnotempty(_sigma_value_0)), '
        '["ValueCount_Target"] = count_distinctif(_sigma_value_1, isnotempty(_sigma_value_1)) '
        "by _sigma_window_end, _sigma_group_0\n"
        f'| where ["ValueCount_User"] {query_operator} {threshold} '
        f'and ["ValueCount_Target"] {query_operator} {threshold}\n'
    ) in query
    assert query.endswith('["DeviceName"] = _sigma_group_0, ["ValueCount_User"], ["ValueCount_Target"]')


def test_multi_field_value_count_maps_each_counted_alias_for_all_source_searches():
    collection = multi_rule_collection(
        source_conditions="[selection, alternate]",
        other_source_conditions=["selection", "selection"],
        counted_field="[Actor, Target]",
        group_by=("Host",),
        aliases=(
            "  aliases:\n    Actor:\n      source-rule: User\n      other-rule: SourceUser\n"
            "    Target:\n      source-rule: File\n      other-rule: Destination\n"
            "    Host:\n      source-rule: Computer\n      other-rule: SourceComputer\n"
        ),
    )
    pipeline = microsoft_365_defender_pipeline()
    pipeline.vars["kusto_field_mappings"] = FieldMappings(
        table_mappings={
            "DeviceProcessEvents": {
                "EventTime": "ProcessTime",
                "User": "ProcessUser",
                "File": "ProcessTarget",
                "Computer": "ProcessHost",
            },
            "DeviceNetworkEvents": {
                "EventTime": "NetworkTime",
                "SourceUser": "NetworkUser",
                "Destination": "NetworkTarget",
                "SourceComputer": "NetworkHost",
            },
        }
    )

    (query,) = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="EventTime").convert(collection)

    assert query.count("(DeviceProcessEvents\n| where ") == 2
    assert query.count("(DeviceNetworkEvents\n| where ") == 2
    assert query.count('_sigma_value_0 = tostring(["ProcessUser"]), _sigma_value_1 = tostring(["ProcessTarget"])') == 2
    assert query.count('_sigma_value_0 = tostring(["NetworkUser"]), _sigma_value_1 = tostring(["NetworkTarget"])') == 2
    assert query.count('todatetime(["ProcessTime"]), _sigma_group_0 = tostring(["ProcessHost"])') == 2
    assert query.count('todatetime(["NetworkTime"]), _sigma_group_0 = tostring(["NetworkHost"])') == 2
    assert query.count('| summarize ["ValueCount_Actor"]') == 1
    assert query.endswith('["Host"] = _sigma_group_0, ["ValueCount_Actor"], ["ValueCount_Target"]')


def test_multi_field_value_count_quotes_metrics_and_preserves_colliding_source_columns_and_generation():
    counted = yaml.safe_dump(['Actor\'s "Name"', "_sigma_value_0"], default_flow_style=True).strip()
    collection = correlation_collection(value_count_correlation(field=counted, generate=True))
    collection.rules[0].fields = ["_sigma_value_0", "_sigma_value_1", "_sigma_values"]
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    standalone, query = backend.convert(collection)

    assert standalone == 'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"'
    assert query.startswith("let _sigma_values_1 = union ")
    assert '_sigma_value_0_1 = tostring(["Actor\'s \\"Name\\""])' in query
    assert '_sigma_value_1_1 = tostring(["_sigma_value_0"])' in query
    assert "| project " not in query.split("| summarize by")[0]
    assert (
        '["ValueCount_Actor\'s \\"Name\\""] = count_distinctif(_sigma_value_0_1, isnotempty(_sigma_value_0_1))' in query
    )
    assert query.endswith('["ValueCount_Actor\'s \\"Name\\""], ["ValueCount__sigma_value_0"]')


@pytest.mark.parametrize("group", ["WindowStart", "WindowEnd", "ValueCount_User", "ValueCount_Target"])
def test_multi_field_value_count_rejects_group_names_that_collide_with_per_field_metrics(group):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    with pytest.raises(SigmaError, match="value_count grouping fields conflict with result columns"):
        backend.convert(correlation_collection(value_count_correlation(field="[User, Target]", group_by=(group,))))


@pytest.mark.parametrize(
    ("operator", "query_operator"),
    [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="), ("eq", "=="), ("neq", "!=")],
)
def test_value_count_applies_each_comparison_after_exact_aggregation(operator, query_operator):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(
        correlation_collection(value_count_correlation(group_by=("DeviceName",), condition=f"{operator}: 2"))
    )

    assert (
        "| summarize ValueCount = count_distinctif(_sigma_value, isnotempty(_sigma_value)) "
        f"by _sigma_window_end, _sigma_group_0\n| where ValueCount {query_operator} 2\n"
    ) in query
    assert query.endswith('["DeviceName"] = _sigma_group_0, ValueCount')


def test_value_count_maps_counted_and_grouping_aliases_per_source_table_and_retains_all_searches():
    collection = multi_rule_collection(
        source_conditions="[selection, alternate]",
        counted_field="Actor",
        group_by=("Host", "DeviceName"),
        aliases=(
            "  aliases:\n    Actor:\n      source-rule: User\n      other-rule: SourceUser\n"
            "    Host:\n      source-rule: Computer\n      other-rule: SourceComputer\n"
        ),
    )
    pipeline = microsoft_365_defender_pipeline()
    pipeline.vars["kusto_field_mappings"] = FieldMappings(
        table_mappings={
            "DeviceProcessEvents": {"EventTime": "ProcessTime", "User": "ProcessUser", "Computer": "ProcessHost"}
        }
    )
    backend = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="EventTime")

    def change_other_mapping(rule, output_format, index, condition, result):
        if rule.name == "other-rule":
            backend.last_processing_pipeline.vars["kusto_field_mappings"] = FieldMappings(
                table_mappings={
                    "DeviceNetworkEvents": {
                        "EventTime": "NetworkTime",
                        "SourceUser": "NetworkUser",
                        "SourceComputer": "NetworkHost",
                    }
                }
            )
        return result

    (query,) = backend.convert(collection, callback=change_other_mapping)

    assert query.count("(DeviceProcessEvents\n| where ") == 2
    assert query.count("(DeviceNetworkEvents\n| where ") == 1
    assert query.count('_sigma_value = tostring(["ProcessUser"])') == 2
    assert '_sigma_value = tostring(["NetworkUser"])' in query
    assert 'todatetime(["ProcessTime"]), _sigma_group_0 = tostring(["ProcessHost"])' in query
    assert 'todatetime(["NetworkTime"]), _sigma_group_0 = tostring(["NetworkHost"])' in query
    assert (
        "| where isnotnull(_sigma_timestamp) and isnotempty(_sigma_group_0) and isnotempty(_sigma_group_1)\n" in query
    )
    assert query.count("| summarize ValueCount") == 1
    assert query.endswith('["Host"] = _sigma_group_0, ["DeviceName"] = _sigma_group_1, ValueCount')


def test_single_source_value_count_aliases_quoted_counted_fields_without_overwriting_source_columns():
    correlation = value_count_correlation(field="Actor") + "  aliases:\n    Actor:\n      source-rule: User\n"
    collection = correlation_collection(correlation, source_conditions="[selection, alternate]")
    collection.rules[0].fields = [
        "_sigma_values",
        "_sigma_value",
        "_sigma_timestamp",
        "_sigma_window_end",
        "_sigma_bin",
    ]
    pipeline = microsoft_365_defender_pipeline()
    pipeline.vars["kusto_field_mappings"] = FieldMappings(generic_mappings={"User": 'Actor\'s "Name"'})
    backend = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp")

    (query,) = backend.convert(collection)

    assert query.startswith("let _sigma_values_1 = union ")
    assert query.count('_sigma_value_1 = tostring(["Actor\'s \\"Name\\""])') == 2
    assert "| project " not in query.split("| summarize by")[0]
    assert "| summarize by _sigma_timestamp_1, _sigma_value_1;" in query
    assert "count_distinctif(_sigma_value_1, isnotempty(_sigma_value_1)) by _sigma_window_end_1" in query
    assert "_sigma_bin_1 = bin(_sigma_timestamp_1, 5m)" in query


@pytest.mark.parametrize("field", ["[]", "''", "42", "[User, '']", "[User, 42]"])
def test_value_count_rejects_invalid_counted_fields(field):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    with pytest.raises(SigmaError, match="value_count requires nonempty counted field names"):
        backend.convert(correlation_collection(value_count_correlation(field=field)))


def test_value_count_rejects_duplicate_counted_fields_with_ambiguous_result_metrics():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    with pytest.raises(SigmaError, match="value_count counted field names must be unique"):
        backend.convert(correlation_collection(value_count_correlation(field="[User, User]")))


def test_value_count_accepts_a_single_field_list_and_preserves_source_generation():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    standalone, query = backend.convert(correlation_collection(value_count_correlation(field="[User]", generate=True)))

    assert standalone == 'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"'
    assert '_sigma_value = tostring(["AccountName"])' in query


@pytest.mark.parametrize("group", ["WindowStart", "WindowEnd", "ValueCount"])
def test_value_count_rejects_group_names_that_collide_with_result_columns(group):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    with pytest.raises(SigmaError, match="value_count grouping fields conflict with result columns"):
        backend.convert(correlation_collection(value_count_correlation(group_by=(group,))))


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
@pytest.mark.parametrize("counted_field", [None, "User", "temporal"])
def test_count_correlations_reject_invalid_source_conversion_state(invalid_state, error, counted_field):
    collection = (
        temporal_collection(third_source=False, source_conditions="[selection, alternate]")
        if counted_field == "temporal"
        else multi_rule_collection(source_conditions="[selection, alternate]", counted_field=counted_field)
    )
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


@pytest.mark.parametrize("correlation", [event_count_correlation, value_count_correlation, temporal_correlation])
def test_count_correlations_require_timestamp_option(correlation):
    backend = KustoBackend(processing_pipeline=microsoft_365_defender_pipeline())

    with pytest.raises(SigmaError, match="correlation_timestamp_field"):
        backend.convert(correlation_collection(correlation(group_by=("DeviceName",))))


@pytest.mark.parametrize("span", ["0m", "-5m"])
@pytest.mark.parametrize("correlation_rule", [event_count_correlation, value_count_correlation, temporal_correlation])
def test_count_correlations_require_a_positive_timespan(span, correlation_rule):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )
    correlation = correlation_rule().replace("5m", span)

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


@pytest.mark.parametrize("parent_rule", [event_count_correlation, value_count_correlation, temporal_correlation])
def test_count_correlations_reject_chained_sources_until_chaining_is_supported(parent_rule):
    child = event_count_correlation().replace("correlation:", "name: child-correlation\ncorrelation:")
    parent = parent_rule().replace("source-rule", "child-correlation")
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
  type: temporal_ordered
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
