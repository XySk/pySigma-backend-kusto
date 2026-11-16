"""Available-feature acceptance preparation; live engine execution is separate."""

from tests.fixtures.correlation.integrated import matrix_fixtures


def test_all_public_producer_consumer_pairs_have_independent_literal_expectations():
    fixtures = matrix_fixtures()
    assert len(fixtures) == 49
    for name, query in fixtures.items():
        assert name.startswith("matrix_")
        assert "datetime(2026-01-01T11:55:00Z)" in query
        assert "datetime(2026-01-01T12:00:00Z)" in query
        assert "Metric:real" in query
        assert "| where Delta!=0" in query


def test_matrix_artifacts_and_manifest_match_current_public_conversion():
    import json
    from pathlib import Path

    from tests.fixtures.correlation.runtime import manifest

    directory = Path(__file__).parent / "fixtures" / "correlation"
    for name, query in matrix_fixtures().items():
        assert (directory / name).read_text() == query
    assert json.loads((directory / "acceptance-manifest.json").read_text()) == manifest(directory)
