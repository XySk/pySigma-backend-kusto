"""Reference-aware relation binding through public collection conversion."""

from tests.correlation_helpers import convert_documents, correlation_document, source_document


def test_single_use_ordinary_context_relations_are_not_materialized():
    summary = correlation_document("summary", "event_count", ["source-rule"])
    summary["fields"] = ["User"]

    (query,) = convert_documents([source_document(), summary])

    assert "let CorrelationMatches = materialize(" in query
    assert "let WindowSourceMatches =\n" in query
    assert "let WindowSourceMatches = materialize(" not in query
    assert "let CompleteContextRelationships = WindowSourceMatches\n" in query
    assert "let CompleteContextRelationships = materialize(" not in query


def test_boolean_context_caches_reused_window_rows_but_not_single_use_participants():
    boolean = {
        "title": "Boolean",
        "correlation": {
            "type": "temporal",
            "condition": "A and B",
            "timespan": "5m",
            "group-by": ["Host"],
        },
        "fields": ["User"],
    }

    (query,) = convert_documents([source_document("A", "A"), source_document("B", "B"), boolean])

    assert "let WindowSourceMatches = materialize(" in query
    assert "let BooleanParticipants = WindowSourceMatches\n" in query
    assert "let BooleanParticipants = materialize(" not in query


def test_two_step_ordered_context_keeps_sort_locality_without_caching_final_participant_filter():
    ordered = correlation_document("ordered", "temporal_ordered", ["A", "B"])
    ordered["fields"] = ["User"]

    (query,) = convert_documents([source_document("A", "A"), source_document("B", "B"), ordered])

    assert "let WindowSourceMatches =\n" in query
    assert "let WindowSourceMatches = materialize(" not in query
    assert "let OrderedSourceRelationships = WindowSourceMatches\n" in query
    assert "let OrderedSourceRelationships = materialize(" not in query
    assert query.index("| sort by GroupHost asc, EventTime_1 asc") < query.index("| scan declare")
