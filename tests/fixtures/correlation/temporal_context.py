"""Literal worked examples for temporal retained-context consumers and chains."""

from tests.correlation_helpers import convert_documents, correlation_document, source_document


def fixture(kind, *, multilevel=False, shared=False):
    source = source_document("raw", "raw")
    source["fields"] = ["User", "Server"]
    child = correlation_document("child", "event_count", ["raw"], groups=("Host",), condition={"gte": 1})
    docs = [source, child, source_document("B", "B")]
    middle = correlation_document("middle", "temporal" if kind == "boolean" else kind, ["child", "B"], groups=("User",))
    if kind == "boolean":
        docs.append(source_document("C", "C"))
        middle["correlation"]["rules"].append("C")
        middle["correlation"]["condition"] = "(child and B) or C"
    docs.append(middle)
    # One child has duplicate evidence and two observed pairs for Alice, plus
    # Bob's independent pair. B arrives later; tied/expired candidates cannot
    # make an ordered completion. C succeeds without B only for Bob.
    rows = [
        'datetime(2026-01-01T12:00:00Z),"h","raw","Alice","s1"',
        'datetime(2026-01-01T12:00:00Z),"h","raw","Alice","s1"',
        'datetime(2026-01-01T12:00:00Z),"h","raw","Alice","s2"',
        'datetime(2026-01-01T12:00:00Z),"h","raw","Bob","b1"',
        'datetime(2026-01-01T12:01:00Z),"d","B","Alice","s3"',
        'datetime(2026-01-01T12:02:00Z),"d","B","Alice","s4"',
    ]
    if kind == "boolean":
        rows += ['datetime(2026-01-01T12:00:00Z),"d","C","Bob","b2"']
    if multilevel:
        refs = ["middle"]
        if shared:
            refs.append("child")
        docs.append(correlation_document("parent", "temporal", refs, groups=("User", "Server")))
        projection = "WindowEnd, User=tostring(User), Server=tostring(Server), RuleCount"
        schema = "WindowEnd:datetime, User:string, Server:string, RuleCount:long"
        expected = []
        # Ordered summaries at minute 2 complete with B@2. B@1 only
        # contributes to the earlier summary and cannot create a new s3 anchor.
        for minute, servers in [
            (1, ["s1", "s2", "s3"]),
            (2, ["s1", "s2", "s4"] if kind == "temporal_ordered" else ["s1", "s2", "s3", "s4"]),
        ]:
            if shared:
                servers = ["s1", "s2"]
            expected += [
                f'datetime(2026-01-01T12:0{minute}:00Z),"Alice","{server}",{2 if shared else 1}' for server in servers
            ]
        if kind == "boolean":
            expected += ['datetime(2026-01-01T12:00:00Z),"Bob","b2",1']
        keys = "WindowEnd,User,Server,RuleCount"
    else:
        # Observe the public collections, not hidden relationship columns.
        projection = "WindowEnd, User=tostring(User), Servers=tostring(array_sort_asc(Server)), RuleCount"
        schema = "WindowEnd:datetime, User:string, Servers:string, RuleCount:long"
        expected = [
            'datetime(2026-01-01T12:01:00Z),"Alice",\'["s1","s2","s3"]\',2',
            (
                'datetime(2026-01-01T12:02:00Z),"Alice",\'["s1","s2","s4"]\',2'
                if kind == "temporal_ordered"
                else 'datetime(2026-01-01T12:02:00Z),"Alice",\'["s1","s2","s3","s4"]\',2'
            ),
        ]
        if kind == "boolean":
            expected += ['datetime(2026-01-01T12:00:00Z),"Bob",\'["b2"]\',2']
        keys = "WindowEnd,User,Servers,RuleCount"
    (query,) = convert_documents(docs)
    return (
        "// Generated with standard collection parsing; expected summaries are literal worked examples.\n"
        "let Events=datatable(Timestamp:datetime,Host:string,Marker:string,User:string,Server:string) [\n"
        + ",\n".join(rows)
        + "\n];\nlet Actual=(){\n"
        + query
        + "\n};\nlet Observed=Actual() | project "
        + projection
        + ";\nlet Expected=datatable("
        + schema
        + ") [\n"
        + ",\n".join(expected)
        + "\n];\nunion (Observed | extend Delta=1), (Expected | extend Delta=-1)\n"
        "| summarize Delta=sum(Delta) by " + keys + "\n| where Delta!=0\n"
    )


def fixtures():
    return {
        "temporal_context_shared.kql": fixture("temporal", multilevel=True, shared=True),
        "temporal_context_unordered.kql": fixture("temporal"),
        "temporal_context_ordered.kql": fixture("temporal_ordered"),
        "temporal_context_boolean.kql": fixture("boolean"),
        "temporal_context_multilevel.kql": fixture("temporal", multilevel=True),
        "temporal_context_boolean_multilevel.kql": fixture("boolean", multilevel=True),
        "temporal_context_ordered_multilevel.kql": fixture("temporal_ordered", multilevel=True),
    }


if __name__ == "__main__":
    from pathlib import Path

    for name, content in fixtures().items():
        (Path(__file__).parent / name).write_text(content)
