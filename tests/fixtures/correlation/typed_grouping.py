"""Typed direct-group acceptance with independently specified expected rows."""

from tests.correlation_helpers import convert_documents, correlation_document, source_document


def fixture(kind="event_count"):
    source = source_document()
    condition = {"gte": 1}
    if kind in ("value_avg", "value_percentile"):
        condition["field"] = "Amount"
    rule = correlation_document("typed", kind, ["source-rule"], groups=("User", "Server"), condition=condition)
    (query,) = convert_documents([source, rule])
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
        """let Events=datatable(Timestamp:datetime,Host:string,Marker:string,User:dynamic,Server:dynamic,Amount:long) [
 datetime(2026-01-01T12:00:00Z),"h","match",dynamic(1),dynamic({"x":[1,"a"]}),2,
 datetime(2026-01-01T12:00:00Z),"h","match",dynamic(1),dynamic({"x":[1,"a"]}),2,
 datetime(2026-01-01T12:00:00Z),"h","match",dynamic("1"),dynamic({"x":[1,"a"]}),3,
 datetime(null),"h","match",dynamic(1),dynamic({"x":[1,"a"]}),100,
 datetime(2026-01-01T12:00:00Z),"h","match",dynamic(null),dynamic({"x":[1,"a"]}),100,
 datetime(2026-01-01T12:00:00Z),"h","match",dynamic(""),dynamic({"x":[1,"a"]}),100
];
let Actual=(){
"""
        + query
        + f"""
}};
let Observed=Actual() | project WindowEnd,Kind=gettype(User),Value=tostring(User),Server=tostring(Server),Metric=toreal({metric});
let Expected=datatable(WindowEnd:datetime,Kind:string,Value:string,Server:string,Metric:real) [
 datetime(2026-01-01T12:00:00Z),"long","1",'{{"x":[1,"a"]}}',{first},
 datetime(2026-01-01T12:00:00Z),"string","1",'{{"x":[1,"a"]}}',{second}
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
        [source_document(), source_document("direct", "direct", product="network"), child, parent, final]
    )
    return (
        """let Events=datatable(Timestamp:datetime,Host:string,Marker:string) [datetime(2026-01-01T12:00:00Z),"h","match"];
let NetworkEvents=datatable(Timestamp:datetime,Marker:string,User:string,Amount:long) [datetime(2026-01-01T12:00:00Z),"direct","1",3];
let Actual=(){
"""
        + query
        + """
};
let Observed=Actual() | project WindowEnd,Kind=gettype(Group),Value=tostring(Group),EventCount;
let Expected=datatable(WindowEnd:datetime,Kind:string,Value:string,EventCount:long) [
 datetime(2026-01-01T12:00:00Z),"long","1",1,
 datetime(2026-01-01T12:00:00Z),"string","1",1
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
    (query,) = convert_documents([source_document(), child, parent])
    return (
        """let Events=datatable(Kind:string) ["bool","datetime","timespan","guid","real","long","string","array","dictionary"]
| extend User=case(Kind=="bool",pack_array(true)[0], Kind=="datetime",pack_array(datetime(2026-01-01))[0], Kind=="timespan",pack_array(1h)[0], Kind=="guid",pack_array(guid(00000000-0000-0000-0000-000000000001))[0], Kind=="real",pack_array(1.0)[0], Kind=="long",dynamic(1), Kind=="string",dynamic("1"),Kind=="array",dynamic([1,"1"]),dynamic({"b":2,"a":1}))
| extend Timestamp=datetime(2026-01-01T12:00:00Z),Marker="match";
let Actual=(){
"""
        + query
        + """
};
let Observed=Actual() | project WindowEnd,Kind=gettype(Actor),EventCount;
let Expected=datatable(Kind:string) ["bool","datetime","timespan","guid","real","long","string","array","dictionary"]
| extend WindowEnd=datetime(2026-01-01T12:00:00Z),EventCount=tolong(1);
union (Observed | extend Delta=1),(Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by WindowEnd,Kind,EventCount
| where Delta!=0
"""
    )
