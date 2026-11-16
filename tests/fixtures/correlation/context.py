"""Retained context fixtures with independent expected summary multisets."""

from tests.correlation_helpers import convert_documents, correlation_document, source_document


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
 datetime(2026-01-01T12:01:00Z), "h", "match", dynamic(1), "one", 10,
 datetime(2026-01-01T12:01:00Z), "h", "match", dynamic("1"), "two", 20,
 datetime(2026-01-01T12:01:00Z), "h", "match", dynamic(1), "one", 10
];
let Actual = () {
"""
        + query
        + f"""
}};
let Observed = Actual()
| mv-apply Pair = _sigma_context_rows on (
    summarize BadPairs = countif(not(
        (tostring(Pair[0][1]) == "one" and gettype(Pair[1][1]) == "long" and tolong(Pair[1][1]) == 1) or
        (tostring(Pair[0][1]) == "two" and gettype(Pair[1][1]) == "string" and tostring(Pair[1][1]) == "1")
    )))
| project WindowEnd, Host=tostring(Host), Metric = toreal({metric}),
    UserCount = array_length(User), ServerCount = array_length(Server), BadPairs,
    UserLong = gettype(User[0]) == "long" or gettype(User[1]) == "long",
    UserString = gettype(User[0]) == "string" or gettype(User[1]) == "string",
    HasServers = set_has_element(Server, "one") and set_has_element(Server, "two");
let Expected = datatable(WindowEnd:datetime, Host:string, Metric:real, UserCount:long, ServerCount:long,
    BadPairs:long, UserLong:bool, UserString:bool, HasServers:bool) [
 datetime(2026-01-01T12:01:00Z), "h", real({expected}), 2, 2, 0, true, true, true
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
    source = source_document()
    source["fields"] = ["User"]
    correlation = correlation_document("nested", "event_count", ["source-rule"], condition={"gte": 1})
    correlation["fields"] = ["Server"]
    (query,) = convert_documents([source, correlation])
    return (
        """// Runtime-typed nested values: bag order, leaf positions, NaN and signed zero.
let Events = datatable(Ix:long) [0,1,2,3,4,5,6,7]
| extend Timestamp=datetime(2026-01-01T12:01:00Z), Host="h", Marker="match"
| extend User=case(
 Ix == 0, bag_pack("a",pack_array(datetime(2026-01-01),1),"b",true),
 Ix == 1, bag_pack("b",true,"a",pack_array(datetime(2026-01-01),1)),
 Ix == 2, pack_array(datetime(2026-01-01), tostring(datetime(2026-01-01))),
 Ix == 3, pack_array(tostring(datetime(2026-01-01)), datetime(2026-01-01)),
 Ix in (4,5), pack_array(real(nan)),
 Ix == 6, pack_array(real(0.0)), pack_array(real(-0.0))),
 Server=case(Ix in (0,1),"bag",Ix in (2,3),"positions",Ix in (4,5),"nan","zero");
let Actual = () {
"""
        + query
        + """
};
let Observed = Actual()
| mv-apply Pair = _sigma_context_rows on (
 summarize BadPairs=countif(not(
  (tostring(Pair[0][1]) == "bag" and gettype(Pair[1][1]) == "dictionary") or
  (tostring(Pair[0][1]) != "bag" and gettype(Pair[1][1]) == "array"))), Pairs=count())
| mv-apply Value=User on (
 summarize Bags=countif(gettype(Value)=="dictionary"),
  DatesFirst=countif(gettype(Value[0])=="datetime"), StringsFirst=countif(gettype(Value[0])=="string"),
  NaNs=countif(isnan(toreal(Value[0]))), Zeroes=countif(gettype(Value[0])=="double" and toreal(Value[0])==0.0))
| project WindowEnd, Host=tostring(Host), EventCount, Values=array_length(User), Pairs, BadPairs, Bags, DatesFirst, StringsFirst, NaNs, Zeroes;
let Expected=datatable(WindowEnd:datetime, Host:string, EventCount:long, Values:long, Pairs:long, BadPairs:long,
 Bags:long, DatesFirst:long, StringsFirst:long, NaNs:long, Zeroes:long) [
 datetime(2026-01-01T12:01:00Z),"h",8,5,8,0,1,1,1,1,1
];
union (Observed | extend Delta=1), (Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by WindowEnd,Host,EventCount,Values,Pairs,BadPairs,Bags,DatesFirst,StringsFirst,NaNs,Zeroes
| where Delta!=0
"""
    )
