"""Typed direct-group acceptance with independently specified expected rows."""

from tests.correlation_helpers import convert_documents, correlation_document, fixture_schema, source_document


def fixture(kind="event_count"):
    source = source_document()
    condition = {"gte": 1}
    if kind in ("value_avg", "value_percentile"):
        condition["field"] = "Amount"
    rule = correlation_document("typed", kind, ["source-rule"], groups=("User", "Server"), condition=condition)
    (query,) = convert_documents([source, rule], schema=fixture_schema(User="long"))
    metric = {
        "event_count": "EventCount",
        "value_count": "ValueCount",
        "value_sum": "ValueSum",
        "value_avg": "ValueAvg",
        "value_percentile": "ValuePercentile",
        "temporal": "RuleCount",
        "temporal_ordered": "RuleCount",
    }[kind]
    first = {
        "event_count": 2,
        "value_count": 1,
        "value_sum": 4,
        "value_avg": 2,
        "value_percentile": 2,
        "temporal": 1,
        "temporal_ordered": 1,
    }[kind]
    second = 1 if kind in ("event_count", "value_count", "temporal", "temporal_ordered") else 3
    return (
        """let Events=datatable(Timestamp:datetime,Host:string,Marker:string,User:long,Server:string,Amount:long) [
 datetime(2026-01-01T12:00:00Z),"h","match",1,"server",2,
 datetime(2026-01-01T12:00:00Z),"h","match",1,"server",2,
 datetime(2026-01-01T12:00:00Z),"h","match",2,"server",3,
 datetime(null),"h","match",1,"server",100,
 datetime(2026-01-01T12:00:00Z),"h","match",long(null),"server",100,
 datetime(2026-01-01T12:00:00Z),"h","match",1,"",100
];
let Actual=(){
"""
        + query
        + f"""
}};
let Observed=Actual() | project WindowEnd,Kind=gettype(User),Value=tostring(User),Server=tostring(Server),Metric=toreal({metric});
let Expected=datatable(WindowEnd:datetime,Kind:string,Value:string,Server:string,Metric:real) [
 datetime(2026-01-01T12:00:00Z),"long","1",'server',{first},
 datetime(2026-01-01T12:00:00Z),"long","2",'server',{second}
];
union (Observed | extend Delta=1),(Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by WindowEnd,Kind,Value,Server,Metric
| where Delta!=0
"""
    )


def fixtures():
    return {
        **{
            f"typed_grouping_{kind}.kql": fixture(kind)
            for kind in (
                "event_count",
                "value_count",
                "value_sum",
                "value_avg",
                "value_percentile",
                "temporal",
                "temporal_ordered",
            )
        },
        "typed_grouping_mixed_chain.kql": mixed_chain_fixture(),
        "typed_grouping_extended_chain.kql": extended_fixture(),
    }


def mixed_chain_fixture():
    child = correlation_document("child", "event_count", ["source-rule"], condition={"gte": 1})
    parent = correlation_document(
        "parent",
        "value_sum",
        ["child", "direct"],
        groups=("Group",),
        field="Amount",
        condition={"gte": 1},
        aliases={
            "Group": {"child": "EventCount", "direct": "User"},
            "Amount": {"child": "EventCount", "direct": "Amount"},
        },
    )
    final = correlation_document("final", "event_count", ["parent"], groups=("Group",), condition={"gte": 1})
    (query,) = convert_documents(
        [source_document(), source_document("direct", "direct", product="network"), child, parent, final],
        schema=fixture_schema(User="long"),
    )
    return (
        """let Events=datatable(Timestamp:datetime,Host:string,Marker:string) [datetime(2026-01-01T12:00:00Z),"h","match"];
let NetworkEvents=datatable(Timestamp:datetime,Marker:string,User:long,Amount:long) [datetime(2026-01-01T12:00:00Z),"direct",1,3];
let Actual=(){
"""
        + query
        + """
};
let Observed=Actual() | project WindowEnd,Kind=gettype(Group),Value=tostring(Group),EventCount;
let Expected=datatable(WindowEnd:datetime,Kind:string,Value:string,EventCount:long) [
 datetime(2026-01-01T12:00:00Z),"long","1",1
];
union (Observed | extend Delta=1),(Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by WindowEnd,Kind,Value,EventCount
| where Delta!=0
"""
    )


def extended_fixture():
    child = correlation_document("child", "event_count", ["source-rule"], groups=("User",), condition={"gte": 1})
    parent = correlation_document(
        "parent",
        "event_count",
        ["child"],
        groups=("Actor",),
        condition={"gte": 1},
        aliases={"Actor": {"child": "User"}},
    )
    (query,) = convert_documents([source_document(), child, parent], schema=fixture_schema(User="guid"))
    return (
        """let Events=datatable(User:guid) [guid(00000000-0000-0000-0000-000000000001)]
| extend Timestamp=datetime(2026-01-01T12:00:00Z),Marker="match";
let Actual=(){
"""
        + query
        + """
};
let Observed=Actual() | project WindowEnd,Kind=gettype(Actor),EventCount;
let Expected=datatable(Kind:string) ["guid"]
| extend WindowEnd=datetime(2026-01-01T12:00:00Z),EventCount=tolong(1);
union (Observed | extend Delta=1),(Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by WindowEnd,Kind,EventCount
| where Delta!=0
"""
    )
