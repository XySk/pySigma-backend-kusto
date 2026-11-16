"""Current-window averages with independently worked literal result multisets."""

from tests.correlation_helpers import convert_documents, correlation_document, fixture_schema, source_document
from tests.fixtures.correlation.generate import HEADER, check


def converted(operator, threshold, kind="real", groups=("Host",)):
    return convert_documents(
        [
            source_document(),
            correlation_document(
                "average",
                "value_avg",
                ["source-rule"],
                groups=groups,
                condition={"field": "Amount", operator: threshold},
            ),
        ],
        schema=fixture_schema(Amount=kind),
    )[0]


def fixtures():
    # No running-total algorithm can recover 3 reliably after subtracting 1e100.
    # Null source matches still anchor windows, including after all usable values expire.
    data = """let Events=datatable(Timestamp:datetime,Host:string,Amount:real,Marker:string) [
 datetime(2026-01-01T00:00:00),"h",real(1e100),"match",
 datetime(2026-01-01T00:01:00),"h",real(3),"match",
 datetime(2026-01-01T00:05:00),"h",real(null),"match",
 datetime(2026-01-01T00:05:00.0000001),"h",real(null),"match",
 datetime(2026-01-01T00:06:00),"h",real(nan),"match",
 datetime(2026-01-01T00:06:00.0000001),"h",real(null),"match",
 datetime(2026-01-01T00:20:00),"empty",real(nan),"match",
 datetime(2026-01-01T00:21:00),"empty",real(+inf),"match",
 datetime(2026-01-01T00:22:00),"empty",real(-inf),"match",
 datetime(2026-01-01T00:23:00),"empty",real(null),"match"
];
"""
    expected = """let Expected=datatable(WindowEnd:datetime,Host:string,ValueAvg:real) [
 datetime(2026-01-01T00:05:00.0000001),"h",3.0,
 datetime(2026-01-01T00:06:00),"h",3.0
] | extend WindowStart=WindowEnd-5m;
"""
    residue = HEADER + data + f"let Actual=(){{\n{converted('eq', 3)}\n}};\n" + expected
    residue += check("Actual()", "Expected", ["WindowStart", "WindowEnd", "Host", "ValueAvg"])

    # Decimal measurements deliberately retain the documented real average output
    # and ordinary double rounding; no tolerance can turn this into equality .15.
    decimals = """let Events=datatable(Timestamp:datetime,Amount:decimal,Marker:string) [
 datetime(2026-01-01T00:00:00),decimal(0.1),"match",
 datetime(2026-01-01T00:00:00),decimal(0.2),"match",
 datetime(2026-01-01T00:01:00),decimal(null),"match"
];
let Expected=datatable(WindowEnd:datetime,ValueAvg:real) [
 datetime(2026-01-01T00:00:00),0.15000000000000002,
 datetime(2026-01-01T00:01:00),0.15000000000000002
] | extend WindowStart=WindowEnd-5m;
"""
    decimal = HEADER + decimals + f"let Actual=(){{\n{converted('neq', .15, 'decimal', ())}\n}};\n"
    decimal += check("Actual()", "Expected", ["WindowStart", "WindowEnd", "ValueAvg"])

    # Numeric strings contribute only after the declared explicit normalization.
    strings = """let Events=datatable(Timestamp:datetime,RawAmount:string,Marker:string) [
 datetime(2026-01-01T00:00:00),"2","match",
 datetime(2026-01-01T00:00:00),"6","match",
 datetime(2026-01-01T00:01:00),"bad","match"
] | extend Amount=toreal(RawAmount);
let Expected=datatable(WindowEnd:datetime,ValueAvg:real) [
 datetime(2026-01-01T00:00:00),4.0,
 datetime(2026-01-01T00:01:00),4.0
] | extend WindowStart=WindowEnd-5m;
"""
    normalized = HEADER + strings + f"let Actual=(){{\n{converted('eq', 4, groups=())}\n}};\n"
    normalized += check("Actual()", "Expected", ["WindowStart", "WindowEnd", "ValueAvg"])
    return {
        "value_avg_expired_large.kql": residue,
        "value_avg_decimal.kql": decimal,
        "value_avg_normalized_strings.kql": normalized,
    }
