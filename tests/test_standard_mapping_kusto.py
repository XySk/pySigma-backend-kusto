import pytest

from sigma.backends.kusto import KustoBackend
from sigma.exceptions import SigmaConversionError
from sigma.processing.pipeline import ProcessingPipeline
from tests.test_correlation_kusto import correlation_collection, multi_rule_collection, value_count_correlation


def mapping_pipeline(mapping):
    return ProcessingPipeline.from_dict(
        {
            "transformations": [
                {"type": "set_state", "key": "query_table", "val": "Events"},
                {"type": "field_name_mapping", "mapping": mapping},
            ]
        }
    )


def test_standard_mapping_resolves_fields_outside_detection_and_preserves_logical_names():
    collection = correlation_collection(
        value_count_correlation(field="User", group_by=("Host",)), source_conditions="[selection, alternate]"
    )
    backend = KustoBackend(
        processing_pipeline=mapping_pipeline(
            {
                "CommandLine": "Process Command",
                "Image": "Process Image",
                "EventTime": "Event Time",
                "Host": "Machine Name",
                "User": "Actor Name",
            }
        ),
        correlation_timestamp_field="EventTime",
    )
    (query,) = backend.convert(collection)
    assert '["Process Command"] =~ "suspicious.exe"' in query
    assert '["Process Image"] =~ "suspicious.exe"' in query
    assert query.count('todatetime(["Event Time"])') == 2
    assert query.count('tostring(["Machine Name"])') == 2
    assert query.count('pack_array(gettype(["Actor Name"]), ["Actor Name"])') == 2
    assert query.endswith('["Host"] = _sigma_group_0, ValueCount')


def test_one_to_many_mapping_rejects_ambiguous_correlation_user_with_resolution_advice():
    backend = KustoBackend(
        processing_pipeline=mapping_pipeline(
            {
                "User": ["ActorUsername", "TargetUsername"],
            }
        ),
        correlation_timestamp_field="Timestamp",
    )
    with pytest.raises(SigmaConversionError, match="User.*ActorUsername.*TargetUsername.*source-specific"):
        backend.convert(correlation_collection(value_count_correlation(field="User")))


def test_source_specific_standard_mapping_and_aliases_use_each_sources_pipeline():
    pipeline = mapping_pipeline({})
    pipeline += ProcessingPipeline.from_dict(
        {
            "transformations": [
                {
                    "type": "field_name_mapping",
                    "mapping": {"User": "Process Actor", "Timestamp": "Process Time", "CommandLine": "Process Command"},
                    "rule_conditions": [{"type": "logsource", "category": "process_creation"}],
                },
                {
                    "type": "field_name_mapping",
                    "mapping": {"SourceUser": "Network Actor", "Timestamp": "Network Time", "DestinationPort": "Port"},
                    "rule_conditions": [{"type": "logsource", "category": "network_connection"}],
                },
            ]
        }
    )
    collection = multi_rule_collection(
        counted_field="Actor",
        group_by=("Actor",),
        aliases="  aliases:\n    Actor:\n      source-rule: User\n      other-rule: SourceUser\n",
    )
    (query,) = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp").convert(collection)
    assert 'todatetime(["Process Time"])' in query
    assert 'todatetime(["Network Time"])' in query
    assert 'tostring(["Process Actor"])' in query
    assert 'tostring(["Network Actor"])' in query
    assert "Port == 443" in query


def test_standard_mapping_sequence_resolves_explicitly_collapsed_one_to_many_targets():
    pipeline = mapping_pipeline({"User": ["Actor", "Target"]})
    pipeline += ProcessingPipeline.from_dict(
        {
            "transformations": [
                {"type": "field_name_mapping", "mapping": {"Actor": "ResolvedUser", "Target": "ResolvedUser"}},
            ]
        }
    )
    (query,) = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp").convert(
        correlation_collection(value_count_correlation(field="User"))
    )
    assert 'pack_array(gettype(["ResolvedUser"]), ["ResolvedUser"])' in query


def test_standard_mapping_of_detection_fields_is_used_for_correlations():
    collection = correlation_collection(value_count_correlation(field="CommandLine"))
    (query,) = KustoBackend(
        processing_pipeline=mapping_pipeline({"CommandLine": "MappedCommand"}), correlation_timestamp_field="Timestamp"
    ).convert(collection)
    assert 'MappedCommand =~ "suspicious.exe"' in query
    assert 'pack_array(gettype(["MappedCommand"]), ["MappedCommand"])' in query


def test_mapped_source_context_retains_requested_logical_names():
    collection = correlation_collection(value_count_correlation(field="User"))
    collection.rules[0].fields = ["User"]
    (query,) = KustoBackend(
        processing_pipeline=mapping_pipeline({"User": "Actor Name"}), correlation_timestamp_field="Timestamp"
    ).convert(collection)
    assert '| project ["WindowStart"], ["WindowEnd"], ["User"], ["ValueCount"]' in query
    assert '["Actor Name"] = ' not in query


def test_standard_mapping_fixture_is_generated_through_public_conversion():
    from pathlib import Path

    from tests.fixtures.correlation.standard_mappings import fixture

    assert Path(__file__).with_name("fixtures").joinpath("correlation/standard_mappings.kql").read_text() == fixture()


def test_builtin_one_to_many_mapping_is_not_silently_reduced_to_first_target():
    from sigma.pipelines.kusto_common.schema import FieldMappings

    pipeline = mapping_pipeline({})
    pipeline.vars["kusto_field_mappings"] = FieldMappings(
        table_mappings={"Events": {"User": ["ActorUsername", "TargetUsername"]}}
    )
    with pytest.raises(SigmaConversionError, match="User.*ActorUsername.*TargetUsername"):
        KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp").convert(
            correlation_collection(value_count_correlation(field="User"))
        )


def test_standard_mapping_preserves_logical_child_group_fields():
    import yaml

    from sigma.collection import SigmaCollection
    from tests.correlation_helpers import correlation_document, source_document

    collection = SigmaCollection.from_yaml(
        yaml.safe_dump_all(
            [
                source_document(),
                correlation_document("child", "event_count", ["source-rule"], condition={"gte": 1}),
                correlation_document("parent", "event_count", ["child"], condition={"gte": 1}),
            ]
        )
    )
    (query,) = KustoBackend(
        processing_pipeline=mapping_pipeline({"Host": "Machine Name"}), correlation_timestamp_field="Timestamp"
    ).convert(collection)
    assert 'tostring(["Machine Name"])' in query
    assert 'todatetime(["WindowEnd"]), _sigma_group_0 = tostring(["Host"])' in query


def test_ordinary_detection_still_supports_one_to_many_and_escapes_identifier_quotes():
    import yaml

    from sigma.collection import SigmaCollection
    from tests.correlation_helpers import source_document

    source = source_document()
    source["detection"]["selection"] = {"User": "Alice"}
    (query,) = KustoBackend(processing_pipeline=mapping_pipeline({"User": ['Actor "Name"', "Target\\Name"]})).convert(
        SigmaCollection.from_yaml(yaml.safe_dump(source))
    )
    assert '["Actor \\"Name\\""] =~ "Alice"' in query
    assert '["Target\\\\Name"] =~ "Alice"' in query
    assert " or " in query


def test_source_context_alias_accepts_uuid_reference_to_named_source():
    correlation = value_count_correlation(field="Actor") + (
        "  aliases:\n    Actor:\n      11111111-1111-1111-1111-111111111111: User\n"
    )
    collection = correlation_collection(correlation)
    collection.rules[0].fields = ["User"]
    (query,) = KustoBackend(
        processing_pipeline=mapping_pipeline({"User": "Actor Name"}), correlation_timestamp_field="Timestamp"
    ).convert(collection)
    assert '| project ["WindowStart"], ["WindowEnd"], ["Actor"], ["ValueCount"]' in query
