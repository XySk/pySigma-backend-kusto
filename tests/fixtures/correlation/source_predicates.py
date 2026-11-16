"""Selected-table predicate regression through ordinary Sigma and real pipelines."""

from pathlib import Path

from sigma.backends.kusto import KustoBackend
from sigma.collection import SigmaCollection
from sigma.pipelines.kusto_common.schema import BaseSchema, FieldInfo, TableSchema
from sigma.pipelines.microsoftxdr import microsoft_xdr_pipeline
from sigma.processing.pipeline import ProcessingPipeline

PRIVILEGED_GROUP_SOURCE = """
title: Privileged group enumeration
name: privileged-groups
logsource:
  product: windows
  service: security
detection:
  selection:
    EventID: 4799
    CallerProcessId: '0x0'
    TargetUserName:
      - Administrators
      - Remote Desktop Users
      - Remote Management Users
      - Distributed COM Users
  condition: selection
"""


def enumeration_query():
    # Standard mappings and a correlation alias resolve independently of XDR.
    source = PRIVILEGED_GROUP_SOURCE.replace("TargetUserName:", "GroupName:").replace("'0x0'", "'0X0'")
    custom = ProcessingPipeline.from_dict(
        {
            "priority": 1,
            "transformations": [
                {"type": "set_state", "key": "query_table", "val": "SecurityEvent"},
                {"type": "field_name_mapping", "mapping": {"Actor": "SubjectUserName", "GroupName": "TargetUserName"}},
                {
                    "type": "replace_string",
                    "regex": "^0X",
                    "replacement": "0x",
                    "field_name_conditions": [{"type": "include_fields", "fields": ["CallerProcessId"]}],
                },
            ],
        }
    )
    pipeline = custom + microsoft_xdr_pipeline()
    pipeline.vars["kusto_schema"] = BaseSchema(
        tables={
            "SecurityEvent": TableSchema(
                fields={
                    "TimeGenerated": FieldInfo("datetime", "Event time"),
                    "SubjectUserName": FieldInfo("string", "Enumerating account"),
                    "TargetUserName": FieldInfo("string", "Enumerated group"),
                }
            )
        }
    )
    correlation = """
---
title: Four distinct privileged groups
correlation:
  type: value_count
  rules: [privileged-groups]
  group-by: [Actor]
  timespan: 5m
  aliases:
    Group:
      privileged-groups: GroupName
  condition:
    field: Group
    gte: 4
"""
    (query,) = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="TimeGenerated").convert(
        SigmaCollection.from_yaml(source + correlation)
    )
    return query


def fixture():
    query = enumeration_query()
    return (
        """// Generated through public conversion; literal expected rows are independently worked.
let SecurityEvent=datatable(TimeGenerated:datetime, EventID:long, CallerProcessId:string,
 SubjectUserName:string, TargetUserName:string, Computer:string) [
 datetime(2026-01-01T12:00:00Z),4799,"0x0","alice","Administrators","A",
 datetime(2026-01-01T12:01:00Z),4799,"0x0","alice","Remote Desktop Users","B",
 datetime(2026-01-01T12:02:00Z),4799,"0x0","alice","Remote Management Users","A",
 datetime(2026-01-01T12:03:00Z),4799,"0x0","alice","Distributed COM Users","B",
 datetime(2026-01-01T12:04:00Z),4799,"0x0","alice","Administrators","A",
 datetime(2026-01-01T12:04:10Z),4624,"0x0","alice","Distributed COM Users","A",
 datetime(2026-01-01T12:04:20Z),4799,"0x1","alice","Distributed COM Users","A",
 datetime(2026-01-01T12:04:30Z),4799,"0x0","alice","Unprivileged","A",
 datetime(2026-01-01T12:00:00Z),4799,"0x0","bob","Administrators","A",
 datetime(2026-01-01T12:01:00Z),4799,"0x0","bob","Remote Desktop Users","A",
 datetime(2026-01-01T12:02:00Z),4799,"0x0","bob","Remote Management Users","A"
];
let Actual=() {
"""
        + query
        + """
};
let ActualRows=materialize(Actual());
let Observed=ActualRows | project WindowStart, WindowEnd, Actor, ValueCount;
let Expected=datatable(WindowStart:datetime, WindowEnd:datetime, Actor:string, ValueCount:long) [
 datetime(2026-01-01T11:58:00Z),datetime(2026-01-01T12:03:00Z),"alice",4,
 datetime(2026-01-01T11:59:00Z),datetime(2026-01-01T12:04:00Z),"alice",4
];
let SchemaObserved=ActualRows | getschema | project ColumnName,ColumnType;
let SchemaExpected=datatable(ColumnName:string, ColumnType:string) [
 "WindowStart","datetime", "WindowEnd","datetime", "Actor","string", "ValueCount","long"
];
union
 (union (Observed | extend Delta=1),(Expected | extend Delta=-1)
  | summarize Delta=sum(Delta) by WindowStart,WindowEnd,Actor,ValueCount
  | where Delta!=0 | project Issue=strcat("rows: ", tostring(pack_all()))),
 (union (SchemaObserved | extend Delta=1),(SchemaExpected | extend Delta=-1)
  | summarize Delta=sum(Delta) by ColumnName,ColumnType
  | where Delta!=0 | project Issue=strcat("schema: ", tostring(pack_all())))
"""
    )


if __name__ == "__main__":
    Path(__file__).with_name("source_predicates_securityevent.kql").write_text(fixture())
