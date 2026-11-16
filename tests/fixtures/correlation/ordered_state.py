"""Strict ordered scan cases with literal, independently worked completions."""

from tests.correlation_helpers import convert_documents, correlation_document, source_document
from tests.fixtures.correlation.generate import HEADER, check


def fixture(*, repeated=False, ungrouped=False):
    # 'shared' satisfies both a and b. Batching must forbid consuming it twice.
    sources = [source_document("a", ["a", "shared"]), source_document("b", ["b", "shared"]), source_document("c", "c")]
    order = ["a", "a", "b"] if repeated else ["a", "b", "c"]
    summary = correlation_document("ordered-state", "temporal_ordered", order, groups=() if ungrouped else ("Host",))
    *_, query = convert_documents([*sources, summary])
    if repeated:
        rows = [
            ("h", "00:00", "a"),
            ("h", "00:00", "a"),
            ("h", "01:00", "b"),
            ("h", "02:00", "shared"),
            ("h", "03:00", "b"),
            ("h", "04:00", "a"),
            ("h", "05:00", "b"),
            ("h", "05:00.0000001", "b"),
        ]
        # At 02, b cannot use a@02. At 05+tick, the newer a@02,a@04 prefix still qualifies.
        completions = [("h", "03:00"), ("h", "05:00"), ("h", "05:00.0000001")]
    else:
        rows = [
            ("h", "00:00", "a"),
            ("h", "01:00", "b"),
            ("h", "02:00", "a"),
            ("h", "03:00", "c"),
            ("h", "04:00", "b"),
            ("h", "05:00", "c"),
            ("h", "07:00", "c"),
            ("h", "07:00.0000001", "c"),
            ("h", "20:00", "shared"),
            ("h", "21:00", "c"),
            ("h", "30:00", "a"),
            ("h", "34:00", "a"),
            ("h", "34:30", "b"),
            ("h", "38:00", "c"),
            ("h", "40:00", "a"),
            ("h", "41:00", "shared"),
            ("h", "41:00", "c"),
            ("h", "42:00", "c"),
        ]
        completions = [("h", minute) for minute in ("03:00", "05:00", "07:00", "38:00", "42:00")]
        if not ungrouped:
            rows += [("z-reset", "42:00", "c"), ("a-first", "00:00", "b"), ("a-first", "01:00", "c")]
    data = ",\n".join(
        f' datetime(2026-01-01T12:{minute}Z), "{host}", "{marker}"' for host, minute, marker in reversed(rows)
    )
    fields = ["WindowStart", "WindowEnd", *([] if ungrouped else ["Host"]), "RuleCount"]
    expected = ",\n".join(
        f" datetime(2026-01-01T12:{minute}Z)" + ("" if ungrouped else f', "{host}"') + f", {len(set(order))}"
        for host, minute in completions
    )
    return (
        HEADER
        + "// Old prefixes survive intervening a; only a new final match completes.\n"
        + "// Literal cases cover total span, strict ties, shared membership, resets and duplicates.\n"
        + "let Events = datatable(Timestamp:datetime, Host:string, Marker:string) [\n"
        + data
        + "\n];\n"
        + "let Actual = () {\n"
        + query
        + "\n};\n"
        + "let CompletionEnds = datatable(WindowEnd:datetime"
        + ("" if ungrouped else ", Host:string")
        + ", RuleCount:long) [\n"
        + expected
        + "\n];\n"
        + "let Expected = CompletionEnds | extend WindowStart=WindowEnd-5m;\n"
        + check("Actual()", "Expected", fields)
    )


def fixtures():
    return {
        "ordered_state_grouped.kql": fixture(),
        "ordered_state_ungrouped.kql": fixture(ungrouped=True),
        "ordered_state_repeated.kql": fixture(repeated=True),
        "ordered_state_real_groups.kql": real_group_fixture(),
    }


def real_group_fixture():
    from tests.correlation_helpers import fixture_schema

    sources = [source_document(marker, marker) for marker in ("a", "b", "c")]
    summary = correlation_document("ordered-real-groups", "temporal_ordered", ["a", "b", "c"], groups=("Group",))
    (query,) = convert_documents([*sources, summary], schema=fixture_schema(Group="real"))
    data = """// Each complete native group has a@0, b@1, c@2. Group 2 has c only.
// The oracle uses native summarize grouping, independently of scan resets.
let Kinds=datatable(Group:real) [real(nan),real(+inf),real(-inf),real(-0.0),real(1.0)];
let Events=union
 (Kinds | extend Timestamp=datetime(2026-01-01T00:00:00),Marker="a"),
 (Kinds | extend Timestamp=datetime(2026-01-01T00:01:00),Marker="b"),
 (Kinds | extend Timestamp=datetime(2026-01-01T00:02:00),Marker="c"),
 (datatable(Group:real,Timestamp:datetime,Marker:string) [2.0,datetime(2026-01-01T00:03:00),"c"]);
let Expected=Events
| summarize RuleCount=count_distinct(Marker),WindowEnd=max(Timestamp) by Group
| where RuleCount==3
| extend WindowStart=WindowEnd-5m;
"""
    return (
        HEADER
        + data
        + "let Actual=(){\n"
        + query
        + "\n};\n"
        + check("Actual()", "Expected", ["WindowStart", "WindowEnd", "Group", "RuleCount"])
    )
