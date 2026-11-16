"""Exact scalar value counts through ordinary collection conversion."""

import pytest

from tests.correlation_helpers import convert_documents, correlation_document, source_document


@pytest.mark.parametrize(
    "operator,symbol", [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="), ("eq", "=="), ("neq", "!=")]
)
def test_scalar_value_count_compares_fractional_thresholds_without_rounding(operator, symbol):
    (query,) = convert_documents(
        [
            source_document(),
            correlation_document("count", "value_count", ["source-rule"], field="User", condition={operator: 2.5}),
        ]
    )

    assert f"todecimal(ValueCount) {symbol} decimal(2.5)" in query


def test_scalar_value_count_uses_narrow_native_sources_and_avoids_window_pair_expansion():
    (query,) = convert_documents(
        [
            source_document(),
            correlation_document(
                "count", "value_count", ["source-rule"], field=["User", "Target"], condition={"gte": 0}
            ),
        ]
    )

    assert (
        '| project EventTime_1 = todatetime(["Timestamp"]), GroupHost = ["Host"], CountedUser = ["User"], CountedTarget = ["Target"]'
        in query
    )
    assert "row_cumsum(" in query
    assert "join kind=inner" not in query and "mv-expand" not in query
    assert "_sigma_identity" not in query and "assert(" not in query
    assert '["ValueCount_User"] >= 0 and ["ValueCount_Target"] >= 0' in query


def test_scalar_value_count_acceptance_fixtures_match_public_conversion():
    from pathlib import Path

    from tests.fixtures.correlation.value_count_sliding import fixtures

    directory = Path(__file__).parent / "fixtures" / "correlation"
    for name, query in fixtures().items():
        assert (directory / name).read_text() == query
