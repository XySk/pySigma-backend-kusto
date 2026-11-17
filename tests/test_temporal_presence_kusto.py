"""Readable exact temporal presence through public collection conversion."""

from tests.correlation_helpers import convert_documents, correlation_document, source_document


def test_temporal_presence_tracks_named_sources_without_pairing_windows():
    documents = [
        source_document("FailedLogon", "failure"),
        source_document("SuccessfulLogon", "success"),
        correlation_document("LogonActivity", "temporal", ["FailedLogon", "SuccessfulLogon"]),
    ]
    (query,) = convert_documents(documents)
    assert "LastFailedLogonTime:datetime" in query
    assert "LastSuccessfulLogonTime:datetime" in query
    assert "HasFailedLogon" in query
    assert "HasSuccessfulLogon" in query
    assert "- 5m" in query
    assert "GroupHost" in query
    assert "join kind=" not in query
    assert "mv-expand" not in query
    assert "make_set" not in query


def test_fractional_rule_count_comparison_preserves_the_fraction():
    documents = [source_document("A", "A"), correlation_document("presence", "temporal", ["A"], condition={"eq": 1.5})]
    (query,) = convert_documents(documents)
    assert "todecimal(RuleCount) == decimal(1.5)" in query


def test_temporal_presence_worked_engine_fixtures_use_public_conversion():
    from pathlib import Path

    from tests.fixtures.correlation.temporal_presence import fixtures

    directory = Path(__file__).parent / "fixtures" / "correlation"
    for name, content in fixtures().items():
        assert (directory / name).read_text() == content


def test_temporal_parent_accepts_child_context_with_scalar_grouping():
    source = source_document("A", "A")
    source["fields"] = ["User"]
    documents = [
        source,
        correlation_document("child", "event_count", ["A"], condition={"gte": 1}),
        correlation_document("parent", "temporal", ["child"]),
    ]
    (query,) = convert_documents(documents)
    assert '["WindowEnd"]' in query
    assert "RuleCount" in query
    assert '["User"]' in query
