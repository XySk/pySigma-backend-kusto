"""Ordered evidence fixtures; expected membership is independently worked by case."""

import json

from tests.correlation_helpers import convert_documents, correlation_document, source_document


def fixture(repeated=False, ungrouped=False, aliases=False):
    sources = [source_document(name, name) for name in ("a", "b", "c")]
    order = ["a", "b", "a"] if repeated else ["a", "b", "c"]
    summary = correlation_document("ordered", "temporal_ordered", order, groups=() if ungrouped else ("Host",))
    summary["fields"] = ["User", "Server"]
    if aliases:
        summary["correlation"]["aliases"] = {"User": {"a": "Actor", "b": "User", "c": "Actor"}}
    *_, query = convert_documents([*sources, summary])
    if repeated:
        rows = [
            (0, "a", "first"),
            (1, "a", "second"),
            (2, "b", "middle"),
            (2, "a", "tie"),
            (3, "a", "last"),
            (4, "a", "overlap"),
        ]
        expected = [(3, ["first", "second", "middle", "last"]), (4, ["first", "second", "middle", "overlap"])]
    else:
        rows = [
            (0, "b", "early"),
            (1, "a", "first"),
            (2, "a", "second"),
            (3, "b", "middle1"),
            (3, "b", "middle1"),
            (4, "b", "middle2"),
            (5, "a", "late"),
            (5, "c", "finish"),
            (6, "c", "overlap"),
            (7, "c", "expired"),
        ]
        expected = [
            (5, ["first", "second", "middle1", "middle2", "finish"]),
            (6, ["first", "second", "middle1", "middle2", "overlap"]),
            (7, ["second", "middle1", "middle2", "expired"]),
        ]
    data = ",\n".join(
        f' datetime(2026-01-01T12:{minute:02}:00Z), "h", "{marker}", "{user}", "{user}", "{user}-server"'
        for minute, marker, user in rows
    )
    if not ungrouped:
        data += (
            ',\n datetime(2026-01-01T12:00:00Z), "span-fail", "a", "bad", "bad", "bad-server",'
            '\n datetime(2026-01-01T12:04:00Z), "span-fail", "b", "bad", "bad", "bad-server",'
            '\n datetime(2026-01-01T12:08:00Z), "span-fail", "c", "bad", "bad", "bad-server",'
            '\n datetime(2026-01-01T12:00:00Z), "tie-fail", "a", "bad", "bad", "bad-server",'
            '\n datetime(2026-01-01T12:00:00Z), "tie-fail", "b", "bad", "bad", "bad-server",'
            '\n datetime(2026-01-01T12:01:00Z), "tie-fail", "c", "bad", "bad", "bad-server"'
        )
    expected_rows = ",\n".join(
        f" datetime(2026-01-01T12:{minute:02}:00Z), '{json.dumps(sorted(users),separators=(',', ':'))}', "
        f"'{json.dumps(sorted(user+'-server' for user in users),separators=(',', ':'))}', {len(users)}, {len(set(order))}, true"
        for minute, users in expected
    )
    return (
        "// Generated via public Sigma parsing and KustoBackend.convert. Zero differences means PASS.\n"
        "let Events=datatable(Timestamp:datetime, Host:string, Marker:string, User:string, Actor:string, Server:string) [\n"
        + data
        + "\n];\nlet Actual=() {\n"
        + query
        + "\n};\n"
        "let Observed=Actual()\n"
        "| project WindowEnd, Users=tostring(array_sort_asc(User)), Servers=tostring(array_sort_asc(Server)), Values=array_length(User), RuleCount, ContextComplete;\n"
        "let Expected=datatable(WindowEnd:datetime, Users:string, Servers:string, Values:long, RuleCount:long, ContextComplete:bool) [\n"
        + expected_rows
        + "\n];\n"
        "union (Observed | extend Delta=1), (Expected | extend Delta=-1)\n"
        "| summarize Delta=sum(Delta) by WindowEnd,Users,Servers,Values,RuleCount,ContextComplete\n| where Delta!=0\n"
    )


def fixtures():
    return {
        "context_ordered_alternatives.kql": fixture(),
        "context_ordered_repeated.kql": fixture(repeated=True),
        "context_ordered_ungrouped.kql": fixture(ungrouped=True),
        "context_ordered_aliases.kql": fixture(aliases=True),
    }
