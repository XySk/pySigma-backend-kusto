"""Source selection must preserve predicates for the actual table."""

import pytest

from sigma.backends.kusto import KustoBackend
from sigma.collection import SigmaCollection
from sigma.pipelines.kusto_common.schema import BaseSchema, FieldInfo, TableSchema
from sigma.pipelines.microsoftxdr import microsoft_xdr_pipeline
from sigma.processing.pipeline import ProcessingPipeline
from tests.fixtures.correlation.source_predicates import PRIVILEGED_GROUP_SOURCE


@pytest.mark.parametrize("explicit_override", [False, True])
def test_security_event_selection_preserves_privileged_group_predicates(explicit_override):
    custom = ProcessingPipeline.from_dict(
        {"priority": 1, "transformations": [{"type": "set_state", "key": "query_table", "val": "SecurityEvent"}]}
    )
    pipeline = custom + microsoft_xdr_pipeline(query_table="SecurityEvent" if explicit_override else None)
    (query,) = KustoBackend(processing_pipeline=pipeline).convert(SigmaCollection.from_yaml(PRIVILEGED_GROUP_SOURCE))
    assert query.startswith("SecurityEvent\n")
    assert "EventID == 4799" in query
    assert 'CallerProcessId =~ "0x0"' in query
    for group in ("Administrators", "Remote Desktop Users", "Remote Management Users", "Distributed COM Users"):
        assert f'"{group}"' in query


def test_non_xdr_table_keeps_native_fields_and_values():
    source = PRIVILEGED_GROUP_SOURCE.replace(
        "    CallerProcessId: '0x0'",
        "    CallerProcessId: '0x0'\n    Image: 'C:\\Windows\\cmd.exe'\n    ParentImage: 'C:\\Windows\\parent.exe'\n    AccountName: 'DOMAIN\\alice'\n    RegistryKey: 'HKLM\\SOFTWARE\\Example'",
    )
    (query,) = KustoBackend(processing_pipeline=microsoft_xdr_pipeline(query_table="SecurityEvent")).convert(
        SigmaCollection.from_yaml(source)
    )
    assert 'Image =~ "C:\\\\Windows\\\\cmd.exe"' in query
    assert 'ParentImage =~ "C:\\\\Windows\\\\parent.exe"' in query
    assert 'AccountName =~ "DOMAIN\\\\alice"' in query
    assert 'RegistryKey =~ "HKLM\\\\SOFTWARE\\\\Example"' in query


def test_non_xdr_correlation_fields_do_not_receive_xdr_generic_mappings():
    correlation = """
---
title: Count users
correlation:
  type: value_count
  rules: [privileged-groups]
  group-by: [Computer]
  timespan: 5m
  condition:
    field: User
    gte: 2
"""
    pipeline = microsoft_xdr_pipeline(query_table="SecurityEvent")
    pipeline.vars["kusto_schema"] = BaseSchema(
        tables={
            "SecurityEvent": TableSchema(
                fields={
                    "TimeGenerated": FieldInfo("datetime", "Event time"),
                    "Computer": FieldInfo("string", "Computer"),
                    "User": FieldInfo("string", "Native user field"),
                }
            )
        }
    )
    (query,) = KustoBackend(
        processing_pipeline=pipeline,
        correlation_timestamp_field="TimeGenerated",
    ).convert(SigmaCollection.from_yaml(PRIVILEGED_GROUP_SOURCE + correlation))
    assert '["User"]' in query
    assert "InitiatingProcessAccountName" not in query


@pytest.mark.parametrize("event_field", ["EventID", "EventCode", "ObjectType"])
def test_genuine_xdr_table_still_translates_event_and_image_predicates(event_field):
    source = f"""
title: Process creation
logsource:
  product: windows
  category: process_creation
detection:
  selection:
    {event_field}: 1
    Image: cmd.exe
  condition: selection
"""
    (query,) = KustoBackend(processing_pipeline=microsoft_xdr_pipeline(query_table="DeviceProcessEvents")).convert(
        SigmaCollection.from_yaml(source)
    )
    assert query == 'DeviceProcessEvents\n| where FolderPath =~ "cmd.exe"'


def test_motivating_correlation_preserves_filters_with_standard_mapping_alias_and_normalization():
    from tests.fixtures.correlation.source_predicates import enumeration_query

    query = enumeration_query()
    assert "EventID == 4799" in query
    assert 'CallerProcessId =~ "0x0"' in query
    assert "TargetUserName" in query
    assert "SubjectUserName" in query
    assert "InitiatingProcessAccountName" not in query


def test_source_predicate_fixture_is_generated_through_public_conversion():
    from pathlib import Path

    from tests.fixtures.correlation.source_predicates import fixture

    assert (
        Path(__file__).with_name("fixtures").joinpath("correlation/source_predicates_securityevent.kql").read_text()
        == fixture()
    )


def test_authentication_user_mapping_targets_account_and_groups_across_computers():
    source = """
title: Failed target authentication
name: failed-authentication
logsource:
  product: windows
  service: security
detection:
  selection:
    EventID: 4625
    User: alice
  condition: selection
---
title: Failed authentication burst
correlation:
  type: event_count
  rules: [failed-authentication]
  group-by: [User]
  timespan: 10m
  condition:
    gte: 10
"""
    custom = ProcessingPipeline.from_dict(
        {
            "transformations": [
                {"type": "set_state", "key": "query_table", "val": "SecurityEvent"},
                {"type": "field_name_mapping", "mapping": {"User": "TargetUserName"}},
            ]
        }
    )
    pipeline = custom + microsoft_xdr_pipeline()
    pipeline.vars["kusto_schema"] = BaseSchema(
        tables={
            "SecurityEvent": TableSchema(
                fields={
                    "TimeGenerated": FieldInfo("datetime", "Event time"),
                    "TargetUserName": FieldInfo("string", "Target account"),
                }
            )
        }
    )
    (query,) = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="TimeGenerated").convert(
        SigmaCollection.from_yaml(source)
    )
    assert "EventID == 4625" in query
    assert 'TargetUserName =~ "alice"' in query
    assert "SubjectUserName" not in query
    assert "InitiatingProcessAccountName" not in query
    assert "Computer" not in query
