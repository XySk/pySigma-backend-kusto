"""Public Sigma collection conversion contracts for numeric sums."""

from itertools import permutations

import pytest
import yaml

from sigma.backends.kusto import KustoBackend
from sigma.collection import SigmaCollection
from sigma.processing.pipeline import ProcessingPipeline
from tests.correlation_helpers import convert_documents, correlation_document, source_document
from tests.test_correlation_kusto import final_summary_projection


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

    assert '_sigma_value = pack_array(gettype(["Amount"]), ["Amount"])' in query
    assert "| where _sigma_timestamp between ((_sigma_window_end - 5m) .. _sigma_window_end)" in query
    assert f"{symbol} decimal(2.5)" in query
    assert f"{symbol} 2.5" in query
    assert final_summary_projection(query).endswith('["Host"] = _sigma_group_0, ValueSum')


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
    seen = []

    def callback(rule, output_format, index, query, result):
        seen.append((rule.name, index))
        return result + "\n| take 1"

    (query,) = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp").convert(
        SigmaCollection.from_yaml(yaml.safe_dump_all(documents)), callback=callback
    )

    assert ("sum", 0) in seen
    assert query.endswith("| take 1")


def test_sum_accepts_kusto_double_values_without_parsing_numeric_strings():
    (query,) = convert_documents(
        [
            source_document(),
            correlation_document("sum", "value_sum", ["source-rule"], condition={"field": "Amount", "gte": 0}),
        ]
    )

    # Kusto gettype() calls real-valued observations "double".
    assert 'in ("real", "double", "decimal") and isfinite(' in query


def test_sum_rejects_integer_overflow_before_exposing_public_results():
    (query,) = convert_documents(
        [
            source_document(),
            correlation_document("sum", "value_sum", ["source-rule"], condition={"field": "Amount", "gte": 0}),
        ]
    )

    assert "sumif(todecimal(" in query
    assert "decimal(-9223372036854775808)" in query
    assert "decimal(9223372036854775807)" in query
    assert '"Kusto value_sum integer overflow"' in query
    # Overflow rows must survive threshold filtering as errors, and the public
    # query must validate the materialized results before exposing summary rows.
    assert '| union (_sigma_sums | where _sigma_real_count == 0 and (isnull(_sigma_integer_sum)' in query
    assert query.index('| project _sigma_correlation_error = "Kusto value_sum integer overflow"') < query.index("| where assert(")
    assert 'let _sigma_guarded_query_rows = materialize(_sigma_guarded_query());' in query
    assert 'make_set_if(_sigma_correlation_error, isnotempty(_sigma_correlation_error), 1048576)' in query
    assert '| where assert(not(set_has_element(_sigma_guarded_query_failure, "Kusto value_sum integer overflow")), "Kusto value_sum integer overflow")' in query
    assert query.index("| where assert(") < query.index("| where isempty(_sigma_correlation_error)")
    assert query.endswith("| project-away _sigma_correlation_error")


@pytest.mark.parametrize("order", list(permutations(range(4))))
def test_sum_chain_converts_in_every_document_order(order):
    source = source_document()
    unrelated = source_document("unrelated", "other")
    child = correlation_document("child", "value_sum", ["source-rule"], condition={"field": "Amount", "gte": 0})
    parent = correlation_document("parent", "value_sum", ["child"], condition={"field": "ValueSum", "gte": 0})

    documents = [parent, unrelated, child, source]
    query = next(
        q for q in convert_documents([documents[index] for index in order]) if 'todatetime(["WindowEnd"])' in q
    )

    assert query.count("| project WindowStart = ") == 2
    assert '_sigma_timestamp = todatetime(["WindowEnd"])' in query
    assert '_sigma_value = pack_array(gettype(["ValueSum"]), ["ValueSum"])' in query
    assert query.count("| extend ValueSum = ") == 2
