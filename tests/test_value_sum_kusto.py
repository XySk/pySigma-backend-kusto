"""Public Sigma collection conversion contracts for numeric sums."""

from itertools import permutations

import pytest
import yaml

from sigma.backends.kusto import KustoBackend
from sigma.collection import SigmaCollection
from sigma.processing.pipeline import ProcessingPipeline
from tests.correlation_helpers import convert_documents, correlation_document, fixture_schema, source_document
from tests.test_correlation_kusto import final_summary_projection, summary_data_query, summary_projections


@pytest.mark.parametrize(
    ("operator", "symbol"),
    [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="), ("eq", "=="), ("neq", "!=")],
)
def test_sum_compares_fractional_threshold_at_each_exact_window_end(operator, symbol):
    documents = [
        source_document(),
        correlation_document(
            "sum",
            "value_sum",
            ["source-rule"],
            groups=("Host",),
            field="Amount",
            condition={"field": "Amount", operator: 2.5},
        ),
    ]

    (query,) = convert_documents(documents)

    assert 'MeasuredAmount = ["Amount"]' in query
    assert f"{symbol} decimal(2.5)" in query
    assert final_summary_projection(query).endswith('["Host"] = GroupHost, ValueSum')


def test_sum_conversion_honors_public_correlation_callback():
    documents = [
        source_document(),
        correlation_document(
            "sum",
            "value_sum",
            ["source-rule"],
            condition={"field": "Amount", "gte": 1},
        ),
    ]
    pipeline = ProcessingPipeline.from_dict(
        {"transformations": [{"type": "set_state", "key": "query_table", "val": "Events"}]}
    )
    from tests.correlation_helpers import fixture_schema

    pipeline.vars["kusto_schema"] = fixture_schema()
    seen = []

    def callback(rule, output_format, index, query, result):
        seen.append((rule.name, index))
        return result + "\n| take 1"

    (query,) = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp").convert(
        SigmaCollection.from_yaml(yaml.safe_dump_all(documents)), callback=callback
    )

    assert ("sum", 0) in seen
    assert query.endswith("| take 1")


@pytest.mark.parametrize("kind", ["int", "long", "real", "decimal"])
def test_sum_uses_declared_native_arithmetic_without_overflow_assertions(kind):
    (query,) = convert_documents(
        [
            source_document(),
            correlation_document("sum", "value_sum", ["source-rule"], condition={"field": "Amount", "gte": 0}),
        ],
        schema=fixture_schema(Amount=kind),
    )
    assert 'MeasuredAmount = ["Amount"]' in query
    assert "pack_array(gettype(" not in query
    assert "assert(" not in query
    assert "integer overflow" not in query
    assert "todecimal(MeasuredAmount)" not in query
    if kind in ("int", "long"):
        assert "row_cumsum(" in query
        assert "| join" not in query
        assert "== 0" in query  # explicit arrival marker retains zero and negative sums
    else:
        assert "ValueSum = sum" in query
        assert "row_cumsum(" not in query
        if kind == "real":
            assert "isfinite(MeasuredAmount)" in query


@pytest.mark.parametrize(("kind", "promoted"), [("int", "long"), ("decimal", "decimal")])
def test_sum_child_exposes_native_promoted_result_type(kind, promoted):
    docs = [
        source_document(),
        correlation_document("child", "value_sum", ["source-rule"], condition={"field": "Amount", "gte": 0}),
        correlation_document("parent", "value_count", ["child"], field="ValueSum", condition={"gte": 1}),
    ]
    (query,) = convert_documents(docs, schema=fixture_schema(Amount=kind))
    assert 'CountedValueSum = ["ValueSum"]' in query
    # A mixed-source parent rejects an unpromoted int against the long child metric.
    docs[-1] = correlation_document(
        "parent",
        "value_sum",
        ["child", "source-rule"],
        condition={"field": "Measurement", "gte": 0},
        aliases={"Measurement": {"child": "ValueSum", "source-rule": "Amount"}},
    )
    if promoted != kind:
        from sigma.exceptions import SigmaConversionError

        with pytest.raises(SigmaConversionError, match="incompatible source types"):
            convert_documents(docs, schema=fixture_schema(Amount=kind))
    else:
        assert "ValueSum" in convert_documents(docs, schema=fixture_schema(Amount=kind))[0]


@pytest.mark.parametrize("order", list(permutations(range(4))))
def test_sum_chain_converts_in_every_document_order(order):
    source = source_document()
    unrelated = source_document("unrelated", "other")
    child = correlation_document("child", "value_sum", ["source-rule"], condition={"field": "Amount", "gte": 0})
    parent = correlation_document("parent", "value_sum", ["child"], condition={"field": "ValueSum", "gte": 0})

    documents = [parent, unrelated, child, source]
    query = next(q for q in convert_documents([documents[index] for index in order]) if '["WindowEnd"]' in q)

    assert len(summary_projections(query)) == 2
    assert 'EventTime = ["WindowEnd"]' in query
    assert 'MeasuredValueSum = ["ValueSum"]' in query
    assert summary_data_query(query).count("| extend ValueSum = ") == 2


def test_supported_native_sum_runtime_fixtures_use_public_conversion():
    from pathlib import Path

    from tests.fixtures.correlation.native_sum import fixtures

    for name, query in fixtures().items():
        assert Path(__file__).with_name("fixtures").joinpath("correlation", name).read_text() == query
