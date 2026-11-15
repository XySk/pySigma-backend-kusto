"""Independent count-parent acceptance examples, through public collection conversion."""

from tests.correlation_helpers import convert_documents, correlation_document, source_document

DATA = """let Events=datatable(Timestamp:datetime,Host:string,Marker:string,User:dynamic,Server:string) [
 datetime(2026-01-01T12:00:00Z),"h","match",dynamic("Alice"),"server1",
 datetime(2026-01-01T12:00:00Z),"h","match",dynamic("Bob"),"server2",
 datetime(2026-01-01T12:00:00Z),"h","match",dynamic("Alice"),"server1",
 datetime(2026-01-01T12:00:00Z),"h2","match",dynamic("Alice"),"server1",
 datetime(2026-01-01T12:00:00Z),"d","direct",dynamic("Alice"),"server1"
];
"""


def fixture(kind="event_count", groups=("User", "Server"), mixed=False):
    child = correlation_document("child", "event_count", ["source-rule"], condition={"gte": 1})
    source = source_document()
    source["fields"] = ["User", "Server"]
    refs = ["child"]
    docs = [source, child]
    if mixed:
        docs.append(source_document("direct", "direct"))
        refs.append("direct")
    parent = correlation_document("parent", kind, refs, groups=groups, condition={"gte": 1})
    docs.append(parent)
    (query,) = convert_documents(docs)
    metric = "EventCount" if kind == "event_count" else "ValueCount"
    group_cols = ", ".join(groups)
    projection = ", ".join(["WindowEnd", *groups, f"Metric={metric}"])
    schema = ", ".join(["WindowEnd:datetime", *(f"{g}:dynamic" for g in groups), "Metric:long"])
    if groups == ("User", "Server"):
        rows = f'datetime(2026-01-01T12:00:00Z),dynamic("Alice"),dynamic("server1"),{3 if mixed else 2}, datetime(2026-01-01T12:00:00Z),dynamic("Bob"),dynamic("server2"),1'
    elif groups == ("Server",):
        rows = (
            'datetime(2026-01-01T12:00:00Z),dynamic("server1"),1, datetime(2026-01-01T12:00:00Z),dynamic("server2"),1'
        )
    else:
        rows = f"datetime(2026-01-01T12:00:00Z),{2}"
    # Kusto grouping cannot use dynamic; diagnostic strings retain these known literal values.
    summary = ", ".join(["WindowEnd", *(f"{g}=tostring({g})" for g in groups), "Metric"])
    keys = ", ".join(["WindowEnd", *groups, "Metric"])
    return (
        DATA
        + "let Actual=(){\n"
        + query
        + f"""\n}};
let Observed=Actual() | project {projection} | project {summary};
let Expected=datatable({schema}) [{rows}] | project {summary};
union (Observed | extend Delta=1), (Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by {keys}
| where Delta!=0
"""
    )


def fixtures():
    return {
        "count_context_pairs.kql": fixture(),
        "count_context_mixed.kql": fixture(mixed=True),
        "count_context_ungrouped.kql": fixture(groups=()),
        "count_context_values.kql": fixture("value_count", groups=()),
        "count_context_restricted.kql": fixture("value_count", groups=("Server",)),
        "count_context_typed.kql": typed_fixture(),
    }


def typed_fixture():
    source = source_document()
    source["fields"] = ["User"]
    child = correlation_document("child", "event_count", ["source-rule"], condition={"gte": 1})
    parent = correlation_document("parent", "event_count", ["child"], groups=("User",), condition={"gte": 1})
    (query,) = convert_documents([source, child, parent])
    return (
        """let Events=datatable(Timestamp:datetime,Host:string,Marker:string,User:dynamic) [
 datetime(2026-01-01T12:00:00Z),"h","match",dynamic(1),
 datetime(2026-01-01T12:00:00Z),"h","match",dynamic("1"),
 datetime(2026-01-01T12:00:00Z),"h","match",dynamic(1)
];
let Actual=(){
"""
        + query
        + """
};
let Observed=Actual() | project WindowEnd, Kind=gettype(User), Value=tostring(User), EventCount;
let Expected=datatable(WindowEnd:datetime,Kind:string,Value:string,EventCount:long) [
 datetime(2026-01-01T12:00:00Z),"long","1",1,
 datetime(2026-01-01T12:00:00Z),"string","1",1
];
union (Observed | extend Delta=1), (Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by WindowEnd,Kind,Value,EventCount
| where Delta!=0
"""
    )
