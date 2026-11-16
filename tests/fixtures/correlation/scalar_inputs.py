"""Explicit source normalization for legacy scalar acceptance datasets.

The raw datatables retain malformed values to exercise unusable-input anchors.
Each emitted source declaration visibly extracts a declared native scalar before
it enters the generated correlation query. Conversion itself never guesses types.
"""

import re
from functools import wraps

from tests.correlation_helpers import fixture_schema


def scalar_inputs(**types):
    def decorate(build):
        @wraps(build)
        def normalized(*args, **kwargs):
            text = build(*args, **kwargs)
            if isinstance(text, dict):
                return {name: scalar_inputs(**types)(lambda query=query: query)() for name, query in text.items()}
            declared = fixture_schema(**types).tables["Events"]

            def source(match):
                table, expression = match.group(1), match.group(2)
                if f"// Normalize {table} scalar fixture inputs" in expression:
                    return match.group(0)
                fields = set(re.findall(r"\b(\w+)\s*:\s*dynamic\b", expression))
                fields.update(
                    field
                    for field in re.findall(r"\b(\w+)\s*:\s*string\b", expression)
                    if declared.get_field_type(field) == "datetime"
                )
                if "BaseEvents" in expression:
                    fields.update(re.findall(r"\b(\w+)\s*:\s*dynamic\b", text[: match.start()]))
                # Some fixtures construct non-finite numbers after the datatable.
                fields.update(re.findall(r"\b(\w+)\s*=\s*(?:pack_array|case)\(", expression))
                changes = []
                for field in sorted(fields):
                    kind = declared.get_field_type(field)
                    if kind is None:
                        continue
                    if kind in ("int", "long", "real", "decimal"):
                        changes.append(
                            f'{field}=iff(gettype({field}) in ("int","long","real","double","decimal"), '
                            f"to{kind}({field}), {kind}(null))"
                        )
                    else:
                        changes.append(f"{field}=to{kind}({field})")
                if not changes:
                    return match.group(0)
                return (
                    f"let {table}={expression}\n// Normalize {table} scalar fixture inputs explicitly.\n"
                    f"| extend {', '.join(changes)};"
                )

            return re.sub(r"let\s+(Events|NetworkEvents|OtherEvents)\s*=\s*([^;]+);", source, text)

        return normalized

    return decorate
