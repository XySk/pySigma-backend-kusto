"""Boolean conditions through public collection parsing and conversion."""

import pytest

from tests.correlation_helpers import convert_documents, source_document


def documents(expression, *, kind="temporal", rules=None):
    correlation = {"type": kind, "timespan": "5m", "group-by": ["Host"], "condition": expression}
    if rules is not None:
        correlation["rules"] = rules
    return [
        *(source_document(name, name) for name in ("A", "B", "C")),
        {"title": "Boolean", "correlation": correlation},
    ]


def test_boolean_presence_uses_upstream_references_and_inclusive_windows():
    (query,) = convert_documents(documents("A and B and not C"))
    assert 'countif(_sigma_rule == "A") > 0' in query
    assert 'countif(_sigma_rule == "B") > 0' in query
    assert 'countif(_sigma_rule == "C") > 0' in query
    assert "not(" in query
    assert "between ((_sigma_window_end - 5m) .. _sigma_window_end)" in query
    assert '["Host"] = _sigma_group_0, RuleCount' in query


@pytest.mark.parametrize(
    ("expression", "predicate"),
    [
        ("A or B and not C", "(_sigma_present or (_sigma_present_1 and not(_sigma_present_2)))"),
        ("(A or B) and C", "((_sigma_present or _sigma_present_1) and _sigma_present_2)"),
        ("not (A and not B)", "not((_sigma_present and not(_sigma_present_1)))"),
        ("A", "_sigma_present"),
    ],
)
def test_boolean_preserves_parser_precedence_and_nesting(expression, predicate):
    # Explicit references also work; inferred references follow expression order.
    refs = list(
        dict.fromkeys(
            token for token in expression.replace("(", " ").replace(")", " ").split() if token in {"A", "B", "C"}
        )
    )
    query = convert_documents(documents(expression, rules=refs))[-1]
    assert f"| where {predicate}\n" in query


def test_boolean_ordered_expression_has_actionable_error():
    with pytest.raises(Exception, match="Boolean expressions require temporal.*full numeric sequence"):
        convert_documents(documents("A and B", kind="temporal_ordered"))


@pytest.mark.parametrize("expression", ["A and", "A xor B", "A and Missing"])
def test_invalid_boolean_input_has_public_parser_error(expression):
    from sigma.exceptions import SigmaError

    with pytest.raises(SigmaError):
        convert_documents(documents(expression))


def test_boolean_child_and_detection_sources_preserve_aliases_and_generation():
    from tests.correlation_helpers import correlation_document

    child = correlation_document("child", "event_count", ["A"], condition={"gte": 1})
    parent = {
        "title": "Parent",
        "correlation": {
            "type": "temporal",
            "condition": "child and not B",
            "timespan": "5m",
            "group-by": ["Actor"],
            "aliases": {"Actor": {"child": "Host", "B": "Device"}},
        },
    }
    (query,) = convert_documents([parent, source_document("B", "B"), child, source_document("A", "A")])
    assert 'todatetime(["WindowEnd"])' in query
    assert 'pack_array(gettype(["Host"]), ["Host"])' in query
    assert 'pack_array(gettype(["Device"]), ["Device"])' in query
    assert 'countif(_sigma_rule == "child") > 0' in query
    assert '["Actor"] = _sigma_group_0, RuleCount' in query


def test_boolean_uuid_reference_has_public_parser_error():
    source = source_document("named", "A")
    source["id"] = "bee76050-4a3f-4fae-91a7-c348a83e789b"
    parent = {"title": "ID reference", "correlation": {"type": "temporal", "condition": source["id"], "timespan": "5m"}}
    from sigma.exceptions import SigmaCorrelationConditionError

    with pytest.raises(SigmaCorrelationConditionError, match="Failed to parse extended condition expression"):
        convert_documents([source, parent])


def test_boolean_generate_keeps_standalone_source_searches():
    docs = documents("A and B and not C")
    docs[-1]["correlation"]["generate"] = True
    queries = convert_documents(docs)
    assert len(queries) == 4
    assert 'countif(_sigma_rule == "C") > 0' in queries[-1]


def test_boolean_prepared_fixtures_are_current_public_conversion_output():
    from pathlib import Path

    from tests.fixtures.correlation.boolean_generate import fixtures

    directory = Path(__file__).parent / "fixtures" / "correlation"
    for filename, content in fixtures().items():
        assert (directory / filename).read_text() == content


def test_boolean_retains_context_without_changing_detection_membership():
    docs = documents("(A and B) or C")
    docs[-1]["fields"] = ["User", "Server"]
    (query,) = convert_documents(docs)
    assert 'countif(_sigma_rule == "A") > 0' in query
    assert '["User"] =' in query
    assert '["Server"] =' in query
    assert 'RuleCount' in query


def test_boolean_context_fixtures_use_public_conversion_and_stay_current():
    from pathlib import Path

    from tests.fixtures.correlation.boolean_context import fixtures

    directory = Path(__file__).parent / "fixtures" / "correlation"
    for filename, content in fixtures().items():
        assert (directory / filename).read_text() == content


@pytest.mark.parametrize("expression", ["not A or B and C", "not (A and not B)", "not not A", "(A and B) or (A and C)"])
def test_boolean_context_nested_conditions_convert_through_public_parser(expression):
    docs = documents(expression)
    refs = {token for token in expression.replace("(", " ").replace(")", " ").split() if token in {"A", "B", "C"}}
    docs = [doc for doc in docs[:-1] if doc["name"] in refs] + [docs[-1]]
    docs[-1]["fields"] = ["User"]
    (query,) = convert_documents(docs)
    assert '["User"] =' in query
    assert "dynamic([])" in query
