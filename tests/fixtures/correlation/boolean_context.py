"""Boolean context runtime fixtures; expected values are literal worked examples."""

import json
from pathlib import Path

from tests.correlation_helpers import convert_documents, correlation_document, source_document
from tests.fixtures.correlation.scalar_inputs import scalar_inputs


@scalar_inputs()
def fixture(expression, cases, *, chain=False):
    refs = [name for name in ("A", "B", "C") if name in expression]
    docs = [source_document(name, name) for name in refs]
    if chain:
        docs[0]["name"] = "raw"
        child = correlation_document(
            "A", "event_count", ["raw"], groups=("Case", "User", "Server"), condition={"gte": 1}
        )
        docs.append(child)
    docs.append(
        {
            "title": "Boolean context",
            "fields": ["User", "Server"],
            "correlation": {"type": "temporal", "condition": expression, "group-by": ["Case"], "timespan": "5m"},
        }
    )
    (query,) = convert_documents(docs)
    rows, expected = [], []
    for case, markers, wanted in cases:
        for marker, user, server in markers:
            rows.append(f'datetime(2026-01-01T12:00:00Z), "{case}", "{marker}", dynamic({user}), "{server}"')
        pairs = json.loads(wanted)
        users = json.dumps(sorted({item[0][1] for item in pairs}))
        servers = json.dumps(sorted({item[1][1] for item in pairs}))
        expected.append(
            f'datetime(2026-01-01T12:00:00Z), "{case}", {len(set(m[0] for m in markers))}, dynamic({users}), dynamic({servers}), true'
        )
    return (
        """// Generated through standard collection parsing and KustoBackend.convert.
// PASS: zero exact multiset differences. See RUNTIME.md.
let Events=datatable(Timestamp:datetime, Case:string, Marker:string, User:dynamic, Server:string) [
"""
        + ",\n".join(rows)
        + "\n];\nlet Actual=(){\n"
        + query
        + """
};
let Observed=Actual()
| project WindowEnd, Case=tostring(Case), RuleCount, Users=tostring(array_sort_asc(User)), Servers=tostring(array_sort_asc(Server)), UserCount=array_length(User), ServerCount=array_length(Server), ContextComplete;
let Expected=datatable(WindowEnd:datetime, Case:string, RuleCount:long, User:dynamic, Server:dynamic, ContextComplete:bool) [
"""
        + ",\n".join(expected)
        + """
]
| project WindowEnd, Case, RuleCount, Users=tostring(array_sort_asc(User)), Servers=tostring(array_sort_asc(Server)), UserCount=array_length(User), ServerCount=array_length(Server), ContextComplete;
union (Observed | extend Delta=1), (Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by WindowEnd, Case, RuleCount, Users, Servers, UserCount, ServerCount, ContextComplete
| where Delta!=0
"""
    )


def pair(user, server):
    # Context order follows the parent's fields declaration; values remain boxed.
    kind = "long" if isinstance(user, int) else "string"
    import json

    return [["string", str(user)], ["string", server]]


def fixtures():
    import json

    def wanted(*values):
        return json.dumps(sorted([pair(*value) for value in values]))

    ab_c = [
        ("failed", [("A", '"ignored"', "bad"), ("C", '"C"', "c")], wanted(("C", "c"))),
        ("both", [("A", "1", "a"), ("B", '"2"', "b"), ("C", '"C"', "c")], wanted(("1", "a"), ("2", "b"), ("C", "c"))),
    ]
    return {
        "context_boolean_alternatives.kql": fixture("(A and B) or C", ab_c),
        "context_boolean_absence.kql": fixture(
            "not A or B and C",
            [
                ("absence", [("B", '"B"', "b"), ("C", '"C"', "c")], wanted(("B", "b"), ("C", "c"))),
                ("none", [("B", '"ignored"', "b")], "[]"),
            ],
        ),
        "context_boolean_nested.kql": fixture(
            "not (A and not B) or C",
            [
                (
                    "nested",
                    [("A", '"ignored"', "bad"), ("B", '"B"', "b"), ("C", '"C"', "c")],
                    wanted(("B", "b"), ("C", "c")),
                ),
            ],
        ),
        "context_boolean_shared.kql": fixture(
            "(A and B) or (A and C)",
            [
                ("shared", [("A", '"A"', "a"), ("C", '"C"', "c")], wanted(("A", "a"), ("C", "c"))),
            ],
        ),
        "context_boolean_precedence.kql": fixture(
            "A or B and not C",
            [
                (
                    "precedence",
                    [("A", '"A"', "a"), ("B", '"ignored"', "bad"), ("C", '"ignored"', "bad")],
                    wanted(("A", "a")),
                ),
            ],
        ),
        "context_boolean_child.kql": fixture(
            "A or B",
            [
                ("mixed", [("A", '"A"', "a"), ("B", '"B"', "b")], wanted(("A", "a"), ("B", "b"))),
            ],
            chain=True,
        ),
    }


if __name__ == "__main__":
    for name, content in fixtures().items():
        (Path(__file__).parent / name).write_text(content)
