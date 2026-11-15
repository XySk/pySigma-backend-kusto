"""Boolean runtime fixtures with literal, independently worked expected rows."""

from pathlib import Path

from tests.correlation_helpers import convert_documents, correlation_document, source_document

HEADER = """// Generated through SigmaCollection.from_yaml and KustoBackend.convert.
// Run the complete file. PASS = zero output rows (exact summary multiset).
// Runtime evidence and engine version: RUNTIME.md.
"""


def fixture(expression, rows, expected, *, chain=False):
    if chain:
        documents = [
            source_document("raw", "A"),
            source_document("B", "B"),
            correlation_document("child", "event_count", ["raw"], condition={"gte": 1}, groups=("Case",)),
        ]
    else:
        # Parentheses can adjoin names; include only sources named in expression.
        refs = [name for name in ("A", "B", "C") if name in expression]
        documents = [source_document(name, name) for name in refs]
    documents.append(
        {
            "title": "Boolean",
            "correlation": {"type": "temporal", "condition": expression, "group-by": ["Case"], "timespan": "5m"},
        }
    )
    (query,) = convert_documents(documents)
    data = "let Events = datatable(Timestamp:datetime, Case:string, Marker:string) [\n" + rows + "\n];\n"
    expected_data = (
        "let Expected = datatable(WindowEnd:datetime, Case:string, RuleCount:long) [\n"
        + expected
        + "\n]\n| extend WindowStart = WindowEnd - 5m;\n"
    )
    return (
        HEADER
        + data
        + "let Actual = () {\n"
        + query
        + "\n};\n"
        + expected_data
        + """union (Actual() | extend Delta = 1), (Expected | extend Delta = -1)
| summarize Delta = sum(Delta) by WindowStart, WindowEnd, Case, RuleCount
| where Delta != 0
"""
    )


def fixtures():
    return {
        "boolean_presence.kql": fixture(
            "A and B and not C",
            """
    datetime(2026-01-01T12:00:00Z), "absence", "A",
    datetime(2026-01-01T12:01:00Z), "absence", "B",
    datetime(2026-01-01T12:01:00Z), "absence", "B",
    datetime(2026-01-01T12:02:00Z), "absence", "A",
    datetime(2026-01-01T12:00:00Z), "expiry-only", "C",
    datetime(2026-01-01T12:01:00Z), "expiry-only", "A",
    datetime(2026-01-01T12:02:00Z), "expiry-only", "B",
    datetime(2026-01-01T12:00:00Z), "endpoint", "C",
    datetime(2026-01-01T12:01:00Z), "endpoint", "A",
    datetime(2026-01-01T12:02:00Z), "endpoint", "B",
    datetime(2026-01-01T12:05:00Z), "endpoint", "B",
    datetime(2026-01-01T12:05:00.0000001Z), "endpoint", "B",
    datetime(2026-01-01T12:00:00Z), "simultaneous", "A",
    datetime(2026-01-01T12:00:00Z), "simultaneous", "B",
    datetime(2026-01-01T12:00:00Z), "simultaneous", "C"
""",
            """
    datetime(2026-01-01T12:01:00Z), "absence", 2,
    datetime(2026-01-01T12:02:00Z), "absence", 2,
    datetime(2026-01-01T12:05:00.0000001Z), "endpoint", 2
""",
        ),
        "boolean_alternatives.kql": fixture(
            "(A and B) or C",
            """
    datetime(2026-01-01T12:00:00Z), "C-alone", "C",
    datetime(2026-01-01T12:01:00Z), "C-alone", "C",
    datetime(2026-01-01T12:00:00Z), "incomplete", "A",
    datetime(2026-01-01T12:01:00Z), "incomplete", "C",
    datetime(2026-01-01T12:02:00Z), "incomplete", "B",
    datetime(2026-01-01T12:00:00Z), "span", "A",
    datetime(2026-01-01T12:05:00Z), "span", "B",
    datetime(2026-01-01T12:05:00.0000001Z), "span", "B"
""",
            """
    datetime(2026-01-01T12:00:00Z), "C-alone", 1,
    datetime(2026-01-01T12:01:00Z), "C-alone", 1,
    datetime(2026-01-01T12:01:00Z), "incomplete", 2,
    datetime(2026-01-01T12:02:00Z), "incomplete", 3,
    datetime(2026-01-01T12:05:00Z), "span", 2
""",
        ),
        "boolean_precedence.kql": fixture(
            "A or B and not C",
            """
    datetime(2026-01-01T12:00:00Z), "negative-anchor", "A",
    datetime(2026-01-01T12:01:00Z), "negative-anchor", "C",
    datetime(2026-01-01T12:02:00Z), "negative-anchor", "B",
    datetime(2026-01-01T12:00:00Z), "B-alone", "B",
    datetime(2026-01-01T12:01:00Z), "B-alone", "C",
    datetime(2026-01-01T12:00:00Z), "C-alone", "C"
""",
            """
    datetime(2026-01-01T12:00:00Z), "negative-anchor", 1,
    datetime(2026-01-01T12:01:00Z), "negative-anchor", 2,
    datetime(2026-01-01T12:02:00Z), "negative-anchor", 3,
    datetime(2026-01-01T12:00:00Z), "B-alone", 1
""",
        ),
        "boolean_nested_not.kql": fixture(
            "not (A and not B)",
            """
    datetime(2026-01-01T12:00:00Z), "nested", "A",
    datetime(2026-01-01T12:01:00Z), "nested", "B",
    datetime(2026-01-01T12:06:00Z), "nested", "A",
    datetime(2026-01-01T12:06:00.0000001Z), "nested", "A",
    datetime(2026-01-01T12:00:00Z), "B-alone", "B"
""",
            """
    datetime(2026-01-01T12:01:00Z), "nested", 2,
    datetime(2026-01-01T12:06:00Z), "nested", 2,
    datetime(2026-01-01T12:00:00Z), "B-alone", 1
""",
        ),
        "boolean_chained.kql": fixture(
            "child and not B",
            """
    datetime(2026-01-01T12:00:00Z), "child", "A",
    datetime(2026-01-01T12:01:00Z), "child", "A",
    datetime(2026-01-01T12:02:00Z), "child", "B",
    datetime(2026-01-01T12:07:00Z), "child", "A",
    datetime(2026-01-01T12:07:00.0000001Z), "child", "A"
""",
            """
    datetime(2026-01-01T12:00:00Z), "child", 1,
    datetime(2026-01-01T12:01:00Z), "child", 1,
    datetime(2026-01-01T12:07:00.0000001Z), "child", 1
""",
            chain=True,
        ),
    }


if __name__ == "__main__":
    for name, content in fixtures().items():
        (Path(__file__).parent / name).write_text(content)
