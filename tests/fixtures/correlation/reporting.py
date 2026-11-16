"""Explicit report intervals, complete dependency lookback, and tick ownership."""

import hashlib
import json
from pathlib import Path

from tests.correlation_helpers import convert_documents, correlation_document, fixture_schema, source_document
from tests.fixtures.correlation.count_existence import documents as logon_documents
from tests.fixtures.correlation.generate import check

START = "2026-01-01T10:18:00Z"
END = "2026-01-01T10:19:00Z"


def options(start=START, end=END, **kwargs):
    return {"correlation_report_start": start, "correlation_report_end": end, **kwargs}


def named_queries(queries):
    return "".join(f"let Actual{index} = () {{\n{query}\n}};\n" for index, query in enumerate(queries))


def early_logon(*, general=False):
    docs = logon_documents(10, child_span="10m", parent_span="10m")
    callback = (lambda rule, output, index, condition, query: query) if general else None
    (query,) = convert_documents(docs, backend_options=options(), callback=callback)
    data = """let Events = union
(range Minute from 0 to 9 step 1 | project Timestamp=datetime(2026-01-01T10:00:00Z) + Minute * 1m, User="target", Marker="failure"),
(datatable(Timestamp:datetime, User:string, Marker:string) [datetime(2026-01-01T10:18:00Z), "target", "success"]);
"""
    expected = """let Expected = datatable(WindowEnd:datetime, User:string, RuleCount:long) [datetime(2026-01-01T10:18:00Z), "target", 2]
| extend WindowStart=WindowEnd-10m;
"""
    return (
        data
        + named_queries([query])
        + expected
        + check("Actual0()", "Expected", ["WindowStart", "WindowEnd", "User", "RuleCount"])
    )


def adjacent_ticks():
    docs = [source_document(), correlation_document("count", "event_count", ["source-rule"], condition={"gte": 1})]
    intervals = [
        ("2026-01-01T10:00:00.0000000Z", "2026-01-01T10:00:00.0000002Z"),
        ("2026-01-01T10:00:00.0000002Z", "2026-01-01T10:00:00.0000004Z"),
    ]
    queries = [convert_documents(docs, backend_options=options(start, end))[0] for start, end in intervals]
    data = """let Events = datatable(Timestamp:datetime, Host:string, Marker:string) [
datetime(2026-01-01T09:55:00.0000000Z), "h", "match",
datetime(2026-01-01T09:55:00.0000001Z), "h", "match",
datetime(2026-01-01T10:00:00.0000000Z), "h", "match",
datetime(2026-01-01T10:00:00.0000001Z), "h", "match",
datetime(2026-01-01T10:00:00.0000002Z), "h", "match",
datetime(2026-01-01T10:00:00.0000003Z), "h", "match",
datetime(2026-01-01T10:00:00.0000004Z), "h", "match"
];
let Expected = datatable(Report:long, WindowEnd:datetime, EventCount:long) [
0, datetime(2026-01-01T10:00:00.0000000Z), 3,
0, datetime(2026-01-01T10:00:00.0000001Z), 3,
1, datetime(2026-01-01T10:00:00.0000002Z), 3,
1, datetime(2026-01-01T10:00:00.0000003Z), 4
] | extend WindowStart=WindowEnd-5m, Host="h";
"""
    return (
        data
        + named_queries(queries)
        + check(
            "union (Actual0() | extend Report=long(0)), (Actual1() | extend Report=long(1))",
            "Expected",
            ["Report", "WindowStart", "WindowEnd", "Host", "EventCount"],
        )
    )


def shared_multilevel(reverse=False):
    source = source_document()
    child = correlation_document("child", "event_count", ["source-rule"], span="3m", condition={"gte": 2})
    near = correlation_document("near", "event_count", ["child"], span="2m", condition={"gte": 1})
    far = correlation_document("far", "event_count", ["child"], span="7m", condition={"gte": 1})
    root = correlation_document("root", "temporal_ordered", ["far", "success"], span="11m")
    docs = [source, source_document("success", "success"), child, near, far, root]
    queries = convert_documents(
        list(reversed(docs)) if reverse else docs, backend_options=options("2026-01-01T10:18:00Z", END)
    )
    # Reverse order changes public output ordering; label queries by their observable metrics.
    near_query = next(query for query in queries if query.rstrip().splitlines()[-2].endswith("EventCount"))
    root_query = next(query for query in queries if "RuleCount = tolong(2)" in query)
    data = """let Events = datatable(Timestamp:datetime, Host:string, Marker:string) [
datetime(2026-01-01T10:00:00Z), "h", "match",
datetime(2026-01-01T10:02:00Z), "h", "match",
datetime(2026-01-01T10:06:00Z), "h", "match",
datetime(2026-01-01T10:08:00Z), "h", "match",
datetime(2026-01-01T10:18:00Z), "h", "success"
];
let Expected = datatable(WindowEnd:datetime, Host:string, RuleCount:long) [datetime(2026-01-01T10:18:00Z), "h", 2] | extend WindowStart=WindowEnd-11m;
"""
    return (
        data
        + named_queries([near_query, root_query])
        + "let NearRows = Actual0() | count;\n"
        + check("Actual1()", "Expected", ["WindowStart", "WindowEnd", "Host", "RuleCount"])
        + '| union (NearRows | where Count != 0 | project WindowStart=datetime(null), WindowEnd=datetime(null), Host="unexpected-near", RuleCount=Count, Delta=long(1))\n'
    )


def context_callback():
    child = correlation_document("child", "event_count", ["source-rule"], span="3m", condition={"gte": 1})
    child["fields"] = ["User"]
    middle = correlation_document("middle", "temporal_ordered", ["child", "success"], span="7m", generate=True)
    root = correlation_document(
        "root", "value_count", ["middle"], span="2m", field="User", condition={"eq": 3}, generate=True
    )

    def callback(rule, output, index, condition, query):
        return (
            query + "\n| extend EventCount=EventCount+10\n| where EventCount == 12" if rule.name == "child" else query
        )

    queries = convert_documents(
        [root, middle, child, source_document("success", "success"), source_document()],
        backend_options=options("2026-01-01T10:08:00Z", "2026-01-01T10:09:00Z", correlation_context_limit=1),
        callback=callback,
    )
    data = """let Events=datatable(Timestamp:datetime,Host:string,Marker:string,User:string) [
datetime(2026-01-01T10:00:00Z), "h", "match", "a",
datetime(2026-01-01T10:01:00Z), "h", "match", "b",
datetime(2026-01-01T10:02:00Z), "h", "match", "c",
datetime(2026-01-01T10:08:00Z), "h", "success", "s"
];
let Expected=datatable(WindowEnd:datetime,Host:string,ValueCount:long) [datetime(2026-01-01T10:08:00Z), "h", 3] | extend WindowStart=WindowEnd-2m;
"""
    # middle.generate also emits its direct success source, which is caller controlled.
    root_query = next(
        query
        for query in queries
        if "| project WindowStart" in query and '"ValueCount"' in query.rsplit("| project", 1)[1]
    )
    middle_query = next(query for query in queries if "RuleCount = tolong(2)" in query and query != root_query)
    child_query = next(
        query
        for query in queries
        if "ContextComplete" in query and "RuleCount = tolong(2)" not in query and query != root_query
    )
    return (
        data
        + named_queries([child_query, middle_query, root_query])
        + "let ChildRows=Actual0() | count;\nlet MiddleInvalid=Actual1() | where ContextComplete or array_length(User) != 1 | count;\n"
        + check(
            "Actual2() | project WindowStart,WindowEnd,Host,ValueCount",
            "Expected",
            ["WindowStart", "WindowEnd", "Host", "ValueCount"],
        )
        + '| union (ChildRows | where Count != 0 | project WindowStart=datetime(null), WindowEnd=datetime(null), Host="child-public", ValueCount=Count, Delta=long(1))\n| union (MiddleInvalid | where Count != 0 | project WindowStart=datetime(null), WindowEnd=datetime(null), Host="middle-display", ValueCount=Count, Delta=long(1))\n'
    )


def collision():
    docs = [source_document(), correlation_document("count", "event_count", ["source-rule"], condition={"gte": 1})]
    (query,) = convert_documents(
        docs, schema=fixture_schema(ReportStart="datetime", ReportEnd="datetime"), backend_options=options()
    )
    return (
        """let Events=datatable(Timestamp:datetime,Host:string,Marker:string,ReportStart:datetime,ReportEnd:datetime) [datetime(2026-01-01T10:18:00Z),"h","match",datetime(2030-01-01),datetime(2020-01-01)];
let Expected=datatable(WindowEnd:datetime,Host:string,EventCount:long) [datetime(2026-01-01T10:18:00Z),"h",1] | extend WindowStart=WindowEnd-5m;
"""
        + named_queries([query])
        + check("Actual0()", "Expected", ["WindowStart", "WindowEnd", "Host", "EventCount"])
    )


def fixtures():
    return {
        "reporting_early_existence.kql": early_logon(),
        "reporting_early_general.kql": early_logon(general=True),
        "reporting_adjacent_ticks.kql": adjacent_ticks(),
        "reporting_shared_multilevel.kql": shared_multilevel(),
        "reporting_shared_reversed.kql": shared_multilevel(reverse=True),
        "reporting_context_callback.kql": context_callback(),
        "reporting_name_collision.kql": collision(),
    }


def write(directory=None):
    directory = Path(directory or Path(__file__).parent)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, query in fixtures().items():
        (directory / name).write_text(query)
        manifest.append({"name": name, "sha256": hashlib.sha256(query.encode()).hexdigest(), "expected_error": None})
    (directory / "reporting-manifest.json").write_text(
        json.dumps({"status": "PENDING", "fixtures": manifest}, indent=2) + "\n"
    )


if __name__ == "__main__":
    write()
