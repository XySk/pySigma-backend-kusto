"""Public conversion contract for readable ordered temporal detection."""

from tests.correlation_helpers import convert_documents, correlation_document, source_document


def test_ordered_detection_uses_batched_readable_state_without_prefix_joins():
    documents = [source_document(name, name) for name in ("failure", "challenge", "success")]
    documents.append(correlation_document("sequence", "temporal_ordered", ["failure", "challenge", "success"]))

    (query,) = convert_documents(documents)

    assert "| scan declare (" in query
    assert "| join " not in query
    assert "LatestFailureThenChallengeStart" in query
    assert "SequenceState.LatestFailureStart" in query
    assert "SequenceState.LatestFailureThenChallengeStart" in query
    assert "| where CompleteNow" in query
    assert "RuleCount = tolong(3)" in query
    assert "Batch equal timestamps" in query
    assert "preceding timestamp" in query


def test_single_position_ordered_detection_needs_only_distinct_group_timestamps():
    (query,) = convert_documents(
        [
            source_document("a", "a"),
            correlation_document("ordered", "temporal_ordered", ["a"]),
        ]
    )

    assert "countif(" not in query
    assert "| scan " not in query
    assert "| sort " not in query
    assert "RuleCount = tolong(1)" in query


def test_ordered_real_groups_preserve_native_nan_identity():
    from tests.correlation_helpers import fixture_schema

    (query,) = convert_documents(
        [
            source_document("a", "a"),
            source_document("b", "b"),
            correlation_document("ordered", "temporal_ordered", ["a", "b"], groups=("Group",)),
        ],
        schema=fixture_schema(Group="real"),
    )

    assert "isnan(GroupGroup)" in query
    assert "isnan(prev(GroupGroup))" in query
