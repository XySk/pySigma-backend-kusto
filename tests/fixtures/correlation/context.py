"""Retained context fixtures with independent expected summary multisets."""

from tests.correlation_helpers import convert_documents, correlation_document, source_document
from tests.fixtures.correlation.scalar_inputs import scalar_inputs


@scalar_inputs()
def fixture(kind="event_count", metric="EventCount", expected=3):
    source = source_document()
    source["fields"] = ["User", "Host", "User"]
    condition = {"gte": 0}
    if kind.startswith("value_"):
        condition["field"] = "Amount"
    if kind == "value_percentile":
        condition["percentile"] = 50
    correlation = correlation_document("context", kind, ["source-rule"], field="Amount", condition=condition)
    correlation["fields"] = ["Server", "User"]
    (query,) = convert_documents([source, correlation])
    return (
        """// Generated through public Sigma parsing and Kusto conversion.
// PASS: zero exact multiset differences. Collections are unordered.
let Events = datatable(Timestamp:datetime, Host:string, Marker:string, User:dynamic, Server:string, Amount:long) [
 datetime(2026-01-01T12:01:00Z), "h", "match", dynamic("1"), "one", 10,
 datetime(2026-01-01T12:01:00Z), "h", "match", dynamic("2"), "two", 20,
 datetime(2026-01-01T12:01:00Z), "h", "match", dynamic("1"), "one", 10
];
let Actual = () {
"""
        + query
        + f"""
}};
let Observed = Actual()
| mv-apply Pair = _sigma_context_rows on (
    summarize BadPairs = countif(not(
        (tostring(Pair[0][1]) == "one" and gettype(Pair[1][1]) == "string" and tostring(Pair[1][1]) == "2") or
        (tostring(Pair[0][1]) == "two" and gettype(Pair[1][1]) == "string" and tostring(Pair[1][1]) == "2")
    )))
| project WindowEnd, Host=tostring(Host), Metric = toreal({metric}),
    UserCount = array_length(User), ServerCount = array_length(Server), BadPairs,
    UserLong = false,
    UserString = gettype(User[0]) == "string" or gettype(User[1]) == "string",
    HasServers = set_has_element(Server, "one") and set_has_element(Server, "two");
let Expected = datatable(WindowEnd:datetime, Host:string, Metric:real, UserCount:long, ServerCount:long,
    BadPairs:long, UserLong:bool, UserString:bool, HasServers:bool) [
 datetime(2026-01-01T12:01:00Z), "h", real({expected}), 2, 2, 0, false, true, true
];
union (Observed | extend Delta=1), (Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by WindowEnd, Host, Metric, UserCount, ServerCount, BadPairs, UserLong, UserString, HasServers
| where Delta != 0
"""
    )


def fixtures():
    return {
        f"context_{kind}.kql": fixture(kind, metric, expected)
        for kind, metric, expected in [
            ("event_count", "EventCount", 3),
            ("value_count", "ValueCount", 2),
            ("value_sum", "ValueSum", 40),
            ("value_avg", "ValueAvg", 40 / 3),
            ("value_percentile", "ValuePercentile", 10),
            ("temporal", "RuleCount", 1),
        ]
    }


def nested_fixture():
    """Explicit extraction replaces obsolete arbitrary nested identity expectations."""
    source = source_document()
    source["fields"] = ["User"]
    correlation = correlation_document("normalized", "event_count", ["source-rule"], condition={"gte": 1})
    correlation["fields"] = ["Server"]
    (query,) = convert_documents([source, correlation])
    return (
        """// Property bags are explicitly normalized before entering correlation.
let Events=datatable(Payload:dynamic) [
 dynamic({"user":"Alice","server":"one"}),
 dynamic({"server":"one","user":"Alice"}),
 dynamic({"user":"Bob","server":"two"})
]
| extend User=tostring(Payload.user),Server=tostring(Payload.server),
 Timestamp=datetime(2026-01-01T12:01:00Z),Host="h",Marker="match";
let Actual=(){
"""
        + query
        + """
};
let Observed=Actual() | project WindowEnd,Host,EventCount,Users=array_length(User),Servers=array_length(Server);
let Expected=datatable(WindowEnd:datetime,Host:string,EventCount:long,Users:long,Servers:long) [
 datetime(2026-01-01T12:01:00Z),"h",3,2,2];
union (Observed | extend Delta=1),(Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by WindowEnd,Host,EventCount,Users,Servers
| where Delta!=0
"""
    )
