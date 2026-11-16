"""Readable temporal evidence through public Sigma collection conversion."""

from tests.correlation_helpers import convert_documents, correlation_document, source_document


def test_ordered_context_names_explain_sources_fields_and_reachability():
    sources = [source_document(name, name) for name in ("InitialFailure", "Retry", "Success")]
    ordered = correlation_document("SignInSequence", "temporal_ordered", [source["name"] for source in sources])
    ordered["fields"] = ["User", "Server"]
    (query,) = convert_documents([*sources, ordered])
    assert "ContextUser" in query and "ContextServer" in query and "ContextGroupHost" in query
    assert "EarliestInitialFailure" in query and "LatestSuccess" in query
    assert "OrderedParticipants" in query
    assert "_sigma_context_" not in query


def test_context_names_remain_valid_and_unique_for_colliding_logical_fields():
    from tests.correlation_helpers import fixture_schema

    summary = correlation_document("summary", "temporal_ordered", ["a"])
    summary["fields"] = ["User", "ContextUser", "Actor Name", "Actor_Name"]
    (query,) = convert_documents(
        [source_document("a", "a"), summary],
        schema=fixture_schema(ContextUser="string", Actor_Name="string"),
    )
    assert 'ContextUser_1 = ["User"]' in query
    assert 'ContextContextUser = ["ContextUser"]' in query
    assert 'ContextActor_Name = ["Actor Name"]' in query
    assert 'ContextActor_Name_1 = ["Actor_Name"]' in query


def test_no_requested_context_avoids_participant_computation():
    summary = correlation_document("summary", "temporal_ordered", ["a", "b"])
    (query,) = convert_documents([source_document("a", "a"), source_document("b", "b"), summary])
    assert "Participants" not in query and "ContextComplete" not in query


def test_participant_runtime_fixtures_match_public_conversion():
    import hashlib
    import json
    from pathlib import Path

    from tests.fixtures.correlation.temporal_participants import fixtures

    directory = Path(__file__).parent / "fixtures" / "correlation"
    hashes = {
        item["name"]: item["sha256"]
        for item in json.loads((directory / "temporal-participants-manifest.json").read_text())["fixtures"]
    }
    for name, query in fixtures().items():
        assert (directory / name).read_text() == query
        assert hashes[name] == hashlib.sha256(query.encode()).hexdigest()
