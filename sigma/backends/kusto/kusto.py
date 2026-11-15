import json
import re
from copy import deepcopy
from typing import ClassVar, Dict, Pattern, Tuple, Type, Union

from sigma.conditions import (
    ConditionAND,
    ConditionFieldEqualsValueExpression,
    ConditionItem,
    ConditionNOT,
    ConditionOR,
)
from sigma.conversion.base import TextQueryBackend
from sigma.conversion.deferred import DeferredQueryExpression
from sigma.conversion.state import ConversionState
from sigma.correlations import (
    CorrelationConditionAND,
    CorrelationConditionNOT,
    CorrelationConditionOR,
    SigmaCorrelationConditionOperator,
    SigmaCorrelationRule,
    SigmaCorrelationType,
    SigmaExtendedCorrelationCondition,
    SigmaRuleReference,
)
from sigma.exceptions import SigmaConversionError
from sigma.types import SigmaCompareExpression, SigmaNumber, SigmaString, SpecialChars


class _CorrelationQueryExpression(str):
    """Quote the source identity supplied by pySigma as a literal Kusto string."""

    def format(self, *, rule, ruleid, query, normalization, **kwargs):
        table = rule.get_conversion_states()[0].processing_state["query_table"]
        prefix = f"{table}\n| where "
        if not query.startswith(prefix):
            query = prefix + query
        return super().format(
            ruleid=ruleid,
            rule_column=normalization.rule_column,
            query=query,
            normalization=normalization,
            **kwargs,
        )


class _CorrelationNormalization(str):
    def __new__(cls, expression, rule_column):
        instance = super().__new__(cls, expression)
        instance.rule_column = rule_column
        return instance


class _CorrelationQuery(str):
    """A generated declaration scope and its final data expression."""

    def __new__(cls, definitions, body):
        instance = super().__new__(cls, definitions + body)
        instance.definitions, instance.body = definitions, body
        return instance

    def __add__(self, suffix):
        return type(self)(self.definitions, self.body + suffix)


class KustoBackend(TextQueryBackend):
    """Microsoft 365 Defender KQL Backend."""

    # The backend generates grouping if required
    name: ClassVar[str] = "Kusto backend"
    identifier: ClassVar[str] = "kusto"
    formats: Dict[str, str] = {
        "default": "Kusto Query Language search strings",
    }

    requires_pipeline: bool = False  # m365 pipeline is automatically applied
    correlation_methods: ClassVar[set[str]] = {"default"}
    default_correlation_method: ClassVar[str] = "default"
    finalize_correlation_subqueries: ClassVar[bool] = True
    correlation_search_multi_rule_expression: ClassVar[str] = "union {queries}"
    correlation_search_multi_rule_query_expression: ClassVar[str] = _CorrelationQueryExpression(
        "({query}{normalization}\n| extend {rule_column} = {ruleid})"
    )
    correlation_search_multi_rule_query_expression_joiner: ClassVar[str] = ",\n"

    # Operator precedence
    parenthesize = True
    precedence: ClassVar[Tuple[Type[ConditionItem], Type[ConditionItem], Type[ConditionItem]]] = (
        ConditionNOT,
        ConditionAND,
        ConditionOR,
    )
    group_expression: ClassVar[str] = (
        "({expr})"  # Expression for precedence override grouping as format string with {expr} placeholder
    )
    # Generated query tokens
    token_separator: str = " "  # separator inserted between all boolean operators
    or_token: ClassVar[str] = "or"
    and_token: ClassVar[str] = "and"
    not_token: ClassVar[str] = "not"
    eq_token: ClassVar[str] = " =~ "  # Token inserted between field and value (without separator)

    # String output
    ## Fields
    ### Quoting
    field_quote: ClassVar[str] = (
        "'"  # Character used to quote field characters if field_quote_pattern matches (or not, depending on field_quote_pattern_negation). No field name quoting is done if not set.
    )
    field_quote_pattern: ClassVar[Pattern] = re.compile(
        "^\\w+$"
    )  # Quote field names if this pattern (doesn't) matches, depending on field_quote_pattern_negation. Field name is always quoted if pattern is not set.
    field_quote_pattern_negation: ClassVar[bool] = (
        True  # Negate field_quote_pattern result. Field name is quoted if pattern doesn't matches if set to True (default).
    )

    ### Escaping
    field_escape: ClassVar[str] = ""  # Character to escape particular parts defined in field_escape_pattern.
    field_escape_quote: ClassVar[bool] = True  # Escape quote string defined in field_quote
    field_escape_pattern: ClassVar[Pattern] = re.compile(
        "\\s"
    )  # All matches of this pattern are prepended with the string contained in field_escape.

    ## Values
    str_quote: ClassVar[str] = '"'  # string quoting character (added as escaping character)
    escape_char: ClassVar[str] = "\\"  # Escaping character for special characters inside string
    wildcard_multi: ClassVar[str] = "*"  # Character used as multi-character wildcard
    wildcard_single: ClassVar[str] = "*"  # Character used as single-character wildcard
    add_escaped: ClassVar[str] = "\\"  # Characters quoted in addition to wildcards and string quote
    filter_chars: ClassVar[str] = ""  # Characters filtered
    bool_values: ClassVar[Dict[bool, str]] = {  # Values to which boolean values are mapped.
        True: "true",
        False: "false",
    }

    # String matching operators. if none is appropriate eq_token is used.
    startswith_expression: ClassVar[str] = "{field} startswith {value}"
    endswith_expression: ClassVar[str] = "{field} endswith {value}"
    contains_expression: ClassVar[str] = "{field} contains {value}"
    wildcard_match_expression: ClassVar[Union[str, None]] = (
        '{field} matches regex "{regex}"'  # Regular expression for wildcard matching when direct equality is not applicable. Fixes #34 if not as_in (logic implemented in convert_condition_as_in_expression)
    )

    # Regular expressions
    re_expression: ClassVar[str] = (
        '{field} matches regex "{regex}"'  # Regular expression query as format string with placeholders {field} and {regex}
    )
    re_escape_char: ClassVar[str] = "\\"  # Character used for escaping in regular expressions
    re_escape: ClassVar[Tuple[str, ...]] = '"'  # List of strings that are escaped
    re_escape_escape_char: bool = True  # If True, the escape character is also escaped

    # cidr expressions
    cidr_wildcard: ClassVar[str] = "*"  # Character used as single wildcard
    cidr_expression: ClassVar[str] = (
        'ipv4_is_in_range({field}, "{value}")'  # CIDR expression query as format string with placeholders {field} = {value}
    )
    cidr_in_list_expression: ClassVar[str] = (
        'ipv4_is_in_any_range({field}, "{value}")'  # CIDR expression query as format string with placeholders {field} = in({list})
    )

    # Numeric comparison operators
    compare_op_expression: ClassVar[str] = (
        "{field} {operator} {value}"  # Compare operation query as format string with placeholders {field}, {operator} and {value}
    )
    # Mapping between CompareOperators elements and strings used as replacement for {operator} in compare_op_expression
    compare_operators: ClassVar[Dict[SigmaCompareExpression.CompareOperators, str]] = {
        SigmaCompareExpression.CompareOperators.LT: "<",
        SigmaCompareExpression.CompareOperators.LTE: "<=",
        SigmaCompareExpression.CompareOperators.GT: ">",
        SigmaCompareExpression.CompareOperators.GTE: ">=",
    }

    # Null/None expressions
    field_null_expression: ClassVar[str] = (
        "isnull({field})"  # Expression for field has null value as format string with {field} placeholder for field name
    )
    field_exists_expression: ClassVar[str] = (
        "isnotempty({field})"  # Expression for field exists as format string with {field} placeholder for field name
    )
    field_not_exists_expression: ClassVar[str] = (
        "isempty({field})"  # Expression for field does not exist as format string with {field} placeholder for field name
    )

    # Field value in list, e.g. "field in (value list)" or "field containsall (value list)"
    convert_or_as_in: ClassVar[bool] = True  # Convert OR as in-expression
    convert_and_as_in: ClassVar[bool] = True  # Convert AND as in-expression
    in_expressions_allow_wildcards: ClassVar[bool] = (
        True  # Values in list can contain wildcards. If set to False (default) only plain values are converted into in-expressions.
    )
    field_in_list_expression: ClassVar[str] = (
        "{field} {op} ({list})"  # Expression for field in list of values as format string with placeholders {field}, {op} and {list}
    )
    or_in_operator: ClassVar[str] = (
        "in~"  # Operator used to convert OR into in-expressions. Must be set if convert_or_as_in is set
    )
    and_in_operator: ClassVar[str] = (
        "has_all"  # Operator used to convert AND into in-expressions. Must be set if convert_and_as_in is set
    )
    list_separator: ClassVar[str] = ", "  # List element separator

    # Value not bound to a field
    unbound_value_str_expression: ClassVar[str] = (
        "{value}"  # Expression for string value not bound to a field as format string with placeholder {value}
    )
    unbound_value_num_expression: ClassVar[str] = (
        "{value}"  # Expression for number value not bound to a field as format string with placeholder {value}
    )
    unbound_value_re_expression: ClassVar[str] = (
        "_=~{value}"  # Expression for regular expression not bound to a field as format string with placeholder {value}
    )

    # Query finalization: appending and concatenating deferred query part
    deferred_start: ClassVar[str] = "\n| "  # String used as separator between main query and deferred parts
    deferred_separator: ClassVar[str] = "\n| "  # String used to join multiple deferred query parts
    deferred_only_query: ClassVar[str] = "*"  # String used as query if final query only contains deferred expression

    # We use =~ for eq_token so everything is case insensitive. But this cannot be used with ints/numbers in queries
    # So we can define a new token to use for SigmaNumeric types and override convert_condition_field_eq_val_num
    # to use it
    num_eq_token: ClassVar[str] = " == "

    # Override methods

    def convert_correlation_ruleid(self, ruleid, template):
        return json.dumps(str(ruleid), ensure_ascii=False)

    def finalize_query(self, rule, query, index, state, output_format):
        # Pipeline state is reset for the next source rule. Keep the mapping used
        # for this search with its conversion state for later normalization.
        state.processing_state["kusto_field_mappings"] = deepcopy(
            self.last_processing_pipeline.vars.get("kusto_field_mappings")
        )
        state.processing_state["kusto_source_fields"] = self._correlation_source_fields(rule, state)
        return super().finalize_query(rule, query, index, state, output_format)

    def _correlation_source_fields(self, rule, state):
        """Gather known source columns without depending on a live Kusto schema."""
        fields = set(rule.fields)

        def detection_fields(detection):
            for item in detection.detection_items:
                if hasattr(item, "detection_items"):
                    detection_fields(item)
                elif item.field is not None:
                    fields.add(item.field)

        for detection in rule.detection.detections.values():
            detection_fields(detection)
        table = state.processing_state.get("query_table")
        mappings = state.processing_state["kusto_field_mappings"]
        if mappings is not None:
            for mapping in (mappings.generic_mappings, mappings.table_mappings.get(table, {})):
                fields.update(mapping)
                for target in mapping.values():
                    fields.update(target if isinstance(target, list) else [target])
        schema = self.last_processing_pipeline.vars.get("kusto_schema")
        if schema is not None:
            fields.update(schema.get_valid_fields(table))
        return fields

    #  For numeric values, need == instead of =~
    def convert_condition_field_eq_val_num(
        self, cond: ConditionFieldEqualsValueExpression, state: ConversionState
    ) -> Union[str, DeferredQueryExpression]:
        """Conversion of field = number value expressions"""
        try:
            return self.escape_and_quote_field(cond.field) + self.num_eq_token + str(cond.value)
        except TypeError:  # pragma: no cover
            raise NotImplementedError("Field equals numeric value expressions are not supported by the backend.")

    def convert_condition_as_in_expression(
        self, cond: Union[ConditionOR, ConditionAND], state: ConversionState
    ) -> Union[str, DeferredQueryExpression]:
        """Overridden method for conversion of field in value list conditions.
        KQL doesn't really use wildcards, so if we have an 'as_in' condition where one or more of the values has a wildcard,
        we can still use the as_in condition, then append on the wildcard value(s) with a startswith, endswith, or contains
        expression
        """

        field = self.escape_and_quote_field(cond.args[0].field)  # type: ignore
        op1 = self.or_in_operator if isinstance(cond, ConditionOR) else self.and_in_operator
        op2 = self.or_token if isinstance(cond, ConditionOR) else self.and_token
        list_nonwildcard = self.list_separator.join(
            [
                self.convert_value_str(arg.value, state)
                for arg in cond.args
                if isinstance(arg, ConditionFieldEqualsValueExpression)
                and (
                    (isinstance(arg.value, SigmaString) and not arg.value.contains_special())
                    or (isinstance(arg.value, SigmaNumber))
                )
            ]
        )
        list_wildcards = [
            arg.value
            for arg in cond.args
            if isinstance(arg, ConditionFieldEqualsValueExpression)
            and isinstance(arg.value, SigmaString)
            and arg.value.contains_special()
        ]
        as_in_expr = ""
        # Convert as_in and wildcard values separately
        if list_nonwildcard:
            as_in_expr = self.field_in_list_expression.format(field=field, op=op1, list=list_nonwildcard)
        wildcard_exprs_list = []
        if list_wildcards:
            for arg in list_wildcards:
                new_cond = ConditionFieldEqualsValueExpression(field=field, value=arg)
                if arg[1:-1].contains_special():  # Wildcard in string, not at start or end.
                    # We need to get rid of all wildcards, and create a 'and contains' for each element in the list
                    expr = f"{self.token_separator}{self.and_token}{self.token_separator}".join(
                        [
                            self.contains_expression.format(
                                field=field, value=self.convert_value_str(SigmaString(str(x)), state)
                            )
                            for x in arg.s
                            if not isinstance(x, SpecialChars)
                        ]
                    )
                    expr = self.group_expression.format(expr=expr)
                else:
                    expr = self.convert_condition_field_eq_val_str(new_cond, state)
                wildcard_exprs_list.append(expr)
        wildcard_exprs = f"{self.token_separator}{op2}{self.token_separator}".join(wildcard_exprs_list)
        if as_in_expr and wildcard_exprs:
            return as_in_expr + self.token_separator + op2 + self.token_separator + wildcard_exprs
        return as_in_expr + wildcard_exprs

    def convert_condition_not(self, cond: ConditionNOT, state: ConversionState) -> Union[str, DeferredQueryExpression]:
        """Conversion of NOT conditions. Overridden to surround the group or expr of the 'not' negation with parens,
        as expected by KQL.
        """
        arg = cond.args[0]
        try:
            if arg.__class__ in self.precedence:  # group if AND or OR condition is negated
                return self.not_token + "(" + str(self.convert_condition_group(arg, state)) + ")"  # type: ignore
            else:
                expr = self.convert_condition(arg, state)  # type: ignore
                if isinstance(expr, DeferredQueryExpression):  # negate deferred expression and pass it to parent
                    return expr.negate()
                else:  # convert negated expression to string
                    return self.not_token + "(" + expr + ")"
        except TypeError:  # pragma: no cover
            raise NotImplementedError("Operator 'not' not supported by the backend")

    def convert_value_str(self, s: Union[SigmaString, SigmaNumber], state: ConversionState) -> str:
        """Convert a SigmaString into a plain string which can be used in query."""
        if not isinstance(s, SigmaString):
            s = SigmaString(str(s))
        converted = super().convert_value_str(s, state)
        # If we have a wildcard in a string, we need to un-escape it
        # See issue #13
        return re.sub(r"\\\*", r"*", converted)

    def convert_correlation_rule(self, rule, output_format=None, method=None, callback=None):
        converters = {
            SigmaCorrelationType.EVENT_COUNT: self.convert_correlation_event_count_rule,
            SigmaCorrelationType.VALUE_COUNT: self.convert_correlation_value_count_rule,
            SigmaCorrelationType.VALUE_SUM: self.convert_correlation_value_sum_rule,
            SigmaCorrelationType.VALUE_AVG: self.convert_correlation_value_avg_rule,
            SigmaCorrelationType.VALUE_PERCENTILE: self.convert_correlation_value_percentile_rule,
            SigmaCorrelationType.TEMPORAL: self.convert_correlation_temporal_rule,
            SigmaCorrelationType.TEMPORAL_ORDERED: self.convert_correlation_temporal_ordered_rule,
        }
        if rule.type not in converters:
            raise SigmaConversionError(
                rule,
                rule.source,
                "Kusto supports only event_count, value_count, value_sum, value_avg, value_percentile, "
                "temporal, and temporal_ordered correlation rules.",
            )
        queries = converters[rule.type](rule, output_format, method or "default")
        if callback is not None:
            states = rule.get_conversion_states()
            converted = [
                (callback(rule, output_format, index, query, query), state)
                for index, (query, state) in enumerate(zip(queries, states))
            ]
            queries = [query for query, _ in converted if query is not None]
            rule.set_conversion_states([state for query, state in converted if query is not None])
        rule.set_conversion_result(queries)
        return queries if rule._output else []

    def convert_correlation_event_count_rule(self, rule, output_format=None, method="default"):
        return self._convert_count_rule(rule)

    def convert_correlation_temporal_rule(self, rule, output_format=None, method="default"):
        return self._convert_count_rule(rule)

    def convert_correlation_temporal_ordered_rule(self, rule, output_format=None, method="default"):
        return self._convert_count_rule(rule)

    def convert_correlation_value_count_rule(self, rule, output_format=None, method="default"):
        fields = rule.condition.fieldref
        if not isinstance(fields, list):
            fields = [fields]
        if not fields or any(not isinstance(field, str) or not field for field in fields):
            raise SigmaConversionError(rule, rule.source, "Kusto value_count requires nonempty counted field names.")
        if len(set(fields)) != len(fields):
            raise SigmaConversionError(rule, rule.source, "Kusto value_count counted field names must be unique.")
        return self._convert_count_rule(rule, fields)

    def convert_correlation_value_sum_rule(self, rule, output_format=None, method="default"):
        field = rule.condition.fieldref if rule.condition is not None else None
        if not isinstance(field, str) or not field:
            raise SigmaConversionError(rule, rule.source, "Kusto value_sum requires one nonempty measured field name.")
        return self._convert_count_rule(rule, [field])

    def convert_correlation_value_avg_rule(self, rule, output_format=None, method="default"):
        field = rule.condition.fieldref if rule.condition is not None else None
        if not isinstance(field, str) or not field:
            raise SigmaConversionError(rule, rule.source, "Kusto value_avg requires one nonempty measured field name.")
        return self._convert_count_rule(rule, [field])

    def convert_correlation_value_percentile_rule(self, rule, output_format=None, method="default"):
        field = rule.condition.fieldref if rule.condition is not None else None
        if not isinstance(field, str) or not field:
            raise SigmaConversionError(
                rule, rule.source, "Kusto value_percentile requires one nonempty measured field name."
            )
        # pySigma coerces selectors with int(), which can discard fractions.
        # Validate the original standard-parser document as well as its result.
        original = rule.custom_attributes.get("correlation", {}).get("condition", {})
        selector = original.get("percentile", rule.condition.percentile)
        if isinstance(selector, bool) or not isinstance(selector, int) or not 0 <= selector <= 100:
            raise SigmaConversionError(
                rule, rule.source, "Kusto value_percentile percentile selector must be an integer from 0 through 100."
            )
        return self._convert_count_rule(rule, [field])

    def _convert_count_rule(self, rule, counted_fields=None):
        # Error rows remain internal to composition. A public query validates
        # them once, without recursively evaluating child assertions at analysis.
        reserved = set(rule.fields or []) | set(rule.group_by or []) | set(counted_fields or [])
        reserved.add(self.backend_options.get("correlation_timestamp_field", ""))
        for alias in rule.aliases:
            reserved.add(alias.alias)
            reserved.update(alias.mapping.values())
        for reference in rule.referenced_rules:
            source = getattr(reference, "rule", None)
            if source is None:
                continue
            reserved.update(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", "\n".join(source.get_conversion_result() or [])))
            for state in source.get_conversion_states() or []:
                reserved.update(self._correlation_source_tables(state))
                reserved.update(state.processing_state.get("kusto_source_fields", ()))
        error = "_sigma_correlation_error"
        while error in reserved:
            error += "_"
        reserved.add(error)
        children = [reference.rule for reference in rule.referenced_rules if isinstance(reference.rule, SigmaCorrelationRule)]
        enabled = bool(children) and bool(rule.fields or any(
            source.fields or any(
                state.processing_state.get("kusto_context_fields")
                or state.processing_state.get("kusto_requested_fields")
                or state.processing_state.get("kusto_error_column")
                for state in source.get_conversion_states() or []
            ) for source in children
        ))
        shared = {"reserved": reserved, "definitions": [], "bindings": {}, "enabled": enabled}
        guards = {"column": error, "relations": [], "checks": [], "inherited": set(), "messages": set()}
        self._correlation_shared_children = shared
        self._correlation_guard_state = guards
        try:
            queries = self._build_count_rule(rule, counted_fields)
            prefix = "".join(shared["definitions"])
            raw_queries = [prefix + query for query in queries]
            checks = tuple(dict.fromkeys([
                *((prefix + check, error) for check in guards["checks"]),
                *sorted(guards["inherited"]),
            ]))
            output = []
            for raw, state in zip(raw_queries, rule.get_conversion_states()):
                public = raw
                if checks:
                    function = "_sigma_guarded_query"
                    while any(name in reserved for name in (function, function + "_rows", function + "_failure")):
                        function += "_"
                    rows, failure = function + "_rows", function + "_failure"
                    reserved.update((function, rows, failure))
                    message = "Kusto correlation validation failed: " + "; ".join(
                        [*sorted(guards["messages"]), "unrecognized error sentinel"]
                    )
                    check_definitions, calls = [], []
                    for check, check_error in checks:
                        check_function = "_sigma_validation_check"
                        while check_function in reserved:
                            check_function += "_"
                        reserved.add(check_function)
                        check_definitions.append(f"let {check_function} = () {{\n{check}\n}};\n")
                        calls.append(f"({check_function}() | project {error} = tostring({check_error}))")
                    public = (
                        f"let {function} = () {{\n{raw}\n}};\n"
                        + "".join(check_definitions)
                        + f"let {rows} = materialize({function}());\n"
                        f"let {failure} = toscalar(union {', '.join(calls)} | summarize countif(isnotempty({error})));\n"
                        f"{rows}\n| where assert({failure} == 0, {json.dumps(message)})"
                    )
                state.processing_state.update({
                    "kusto_raw_query": raw,
                    "kusto_public_query": public,
                    "kusto_error_column": error if checks else None,
                    "kusto_guard_queries": tuple(check for check, _ in checks),
                    "kusto_guard_error_columns": tuple(column for _, column in checks),
                    "kusto_guard_messages": tuple(sorted(guards["messages"])),
                })
                output.append(public)
            return output
        finally:
            del self._correlation_shared_children, self._correlation_guard_state

    def _register_correlation_guard(self, relation, violation, message):
        column = self._correlation_guard_state["column"]
        self._correlation_guard_state["messages"].add(message)
        self._register_correlation_errors(
            f"{relation} | where {violation} | project {column} = {json.dumps(message)}"
        )

    def _register_correlation_errors(self, relation):
        self._correlation_guard_state["relations"].append(relation)

    def _collect_correlation_checks(self, queries):
        relations = self._correlation_guard_state["relations"]
        self._correlation_guard_state["relations"] = []
        if relations:
            errors = ", ".join(f"({relation})" for relation in relations)
            for query in queries:
                if not isinstance(query, _CorrelationQuery):
                    raise SigmaConversionError(
                        None, None, "Kusto correlation validation requires a generated declaration scope."
                    )
                self._correlation_guard_state["checks"].append(query.definitions + f"union {errors}")
        return queries

    @staticmethod
    def _child_raw_query(query, state):
        raw, checked = state.get("kusto_raw_query"), state.get("kusto_public_query")
        checks, error = state.get("kusto_guard_queries"), state.get("kusto_error_column")
        messages = state.get("kusto_guard_messages")
        columns = state.get("kusto_guard_error_columns")
        valid = (
            query == checked and isinstance(raw, str) and bool(raw)
            and isinstance(checks, (tuple, list)) and all(isinstance(check, str) and check for check in checks)
            and isinstance(columns, (tuple, list)) and len(columns) == len(checks)
            and all(isinstance(column, str) and column for column in columns)
            and isinstance(messages, (tuple, list)) and all(isinstance(message, str) and message for message in messages)
            and ((checks and isinstance(error, str) and bool(error) and bool(messages))
                 or (not checks and error is None and not messages))
        )
        # Callback-modified or incomplete metadata keeps the checked public
        # query. Never bypass its validation or silently discard a source edit.
        return (raw, tuple(zip(checks, columns)), messages) if valid else (query, (), ())

    def _shared_child_source(self, reference_index, query_index, query):
        shared = self._correlation_shared_children
        key = (reference_index, query_index)
        if key not in shared["bindings"]:
            def allocate(base):
                candidate, suffix = base, 0
                while candidate in shared["reserved"]:
                    suffix += 1
                    candidate = f"{base}_{suffix}"
                shared["reserved"].add(candidate)
                return candidate

            function, results = allocate("_sigma_shared_child"), allocate("_sigma_shared_results")
            shared["definitions"].extend([
                f"let {function} = () {{\n{query}\n}};\n",
                f"let {results} = materialize({function}());\n",
            ])
            shared["bindings"][key] = (function, results)
        return shared["bindings"][key]

    def _build_count_rule(self, rule, counted_fields=None):
        count_type = rule.type.name.lower()
        if rule.type in (SigmaCorrelationType.TEMPORAL, SigmaCorrelationType.TEMPORAL_ORDERED):
            metrics = ["RuleCount"]
        elif rule.type == SigmaCorrelationType.VALUE_PERCENTILE:
            metrics = ["ValuePercentile"]
        elif rule.type == SigmaCorrelationType.VALUE_SUM:
            metrics = ["ValueSum"]
        elif rule.type == SigmaCorrelationType.VALUE_AVG:
            metrics = ["ValueAvg"]
        elif counted_fields is None:
            metrics = ["EventCount"]
        elif len(counted_fields) == 1:
            metrics = ["ValueCount"]
        else:
            metrics = [f"ValueCount_{field}" for field in counted_fields]
        if rule.timespan.seconds <= 0:
            raise SigmaConversionError(rule, rule.source, "Kusto correlations require a positive timespan.")
        conflicting_fields = {"WindowStart", "WindowEnd", *metrics}.intersection(rule.group_by or [])
        if conflicting_fields:
            raise SigmaConversionError(
                rule,
                rule.source,
                f"Kusto {count_type} grouping fields conflict with result columns: "
                + ", ".join(sorted(conflicting_fields)),
            )
        if not rule.referenced_rules:
            raise SigmaConversionError(
                rule, rule.source, f"Kusto {count_type} correlations must reference at least one source rule."
            )
        condition_operators = {
            SigmaCorrelationConditionOperator.GT: ">",
            SigmaCorrelationConditionOperator.GTE: ">=",
            SigmaCorrelationConditionOperator.LT: "<",
            SigmaCorrelationConditionOperator.LTE: "<=",
            SigmaCorrelationConditionOperator.EQ: "==",
            SigmaCorrelationConditionOperator.NEQ: "!=",
        }
        boolean = isinstance(rule.condition, SigmaExtendedCorrelationCondition)
        if boolean and rule.type != SigmaCorrelationType.TEMPORAL:
            raise SigmaConversionError(
                rule,
                rule.source,
                "Kusto Boolean expressions require temporal; temporal_ordered requires a full numeric sequence condition.",
            )
        query_operator = ">=" if boolean or rule.condition is None else condition_operators.get(rule.condition.op)
        if query_operator is None:
            raise SigmaConversionError(
                rule,
                rule.source,
                f"Kusto {count_type} correlations require one of the single gt, gte, lt, lte, eq, or neq conditions.",
            )

        timestamp_field = self.backend_options.get("correlation_timestamp_field")
        if not timestamp_field:
            raise SigmaConversionError(
                rule,
                rule.source,
                f"The correlation_timestamp_field backend option is required for {count_type} correlations.",
            )

        typed_groups = bool(rule.group_by)
        self._correlation_typed_groups = typed_groups
        try:
            queries = self._convert_count(rule, timestamp_field, query_operator, counted_fields, metrics)
        finally:
            del self._correlation_typed_groups
        if typed_groups:
            restored = []
            for query in queries:
                for field, lookup, identity, value in self._correlation_group_lookups:
                    quoted = self._quote_correlation_field(field)
                    query += (
                        f"\n| join kind=inner ({lookup} | project {quoted} = {identity}, {value}) on {quoted}"
                        f"\n| extend {quoted} = {value}[1]\n| project-away {value}"
                    )
                query += "\n| project " + ", ".join(
                    self._quote_correlation_field(field)
                    for field in ["WindowStart", "WindowEnd", *(rule.group_by or []), *metrics]
                )
                restored.append(query)
            queries = restored
        queries = self._collect_correlation_checks(queries)
        requested_context = list(rule.fields or [])
        for reference in rule.referenced_rules:
            source = reference.rule
            # Carry requests through summaries, including requests inherited
            # from descendants and fields currently represented as scalar groups.
            source_requests = source.fields or []
            if isinstance(source, SigmaCorrelationRule):
                source_requests = source.get_conversion_states()[0].processing_state.get(
                    "kusto_requested_fields", source_requests
                )
            for field in source_requests:
                logical = next((alias.alias for alias in rule.aliases if alias.mapping.get(reference) == field), field)
                requested_context.append(logical)
        context_fields = list(dict.fromkeys(requested_context))
        conflicts = {"WindowStart", "WindowEnd", *metrics}.intersection(context_fields)
        if conflicts:
            raise SigmaConversionError(
                rule,
                rule.source,
                "Kusto retained context fields conflict with result columns: "
                + ", ".join(sorted(conflicts))
                + ". Use an explicit correlation alias, for example fields: [OriginalWindowEnd] and "
                "aliases: {OriginalWindowEnd: {source-rule: WindowEnd}}.",
            )
        requested_fields = tuple(context_fields)
        context_fields = [field for field in context_fields if field not in (rule.group_by or [])]
        context_column = None
        if context_fields:
            queries, context_column = self._retain_context(rule, queries, context_fields, metrics)
        queries = self._collect_correlation_checks(queries)
        result_fields = {"WindowStart", "WindowEnd", *(rule.group_by or []), *metrics, *context_fields}
        if context_column:
            result_fields.add(context_column)
        source_tables = set().union(
            *(
                self._correlation_source_tables(state)
                for reference in rule.referenced_rules
                for state in reference.rule.get_conversion_states()
            )
        )
        # Summaries expose their result columns, independent of source tables
        # and the pipeline mappings that produced their underlying events.
        rule.set_conversion_states(
            [
                ConversionState(
                    processing_state={
                        "kusto_result_fields": result_fields.copy(),
                        "kusto_source_fields": result_fields.copy(),
                        "kusto_result_timestamp": "WindowEnd",
                        "kusto_requested_fields": requested_fields,
                        "kusto_context_fields": tuple(context_fields),
                        "kusto_context_rows": context_column,
                        "kusto_source_tables": source_tables.copy(),
                    }
                )
                for _ in queries
            ]
        )
        return queries

    @staticmethod
    def _uses_context_groups(rule, fields=None):
        for reference in rule.referenced_rules:
            source = reference.rule
            if not isinstance(source, SigmaCorrelationRule):
                continue
            state = source.get_conversion_states()[0].processing_state
            for field in (rule.group_by or []) if fields is None else fields:
                mapped = next(
                    (
                        alias.mapping.get(
                            reference,
                            next(
                                (
                                    name
                                    for source_reference, name in alias.mapping.items()
                                    if source_reference.reference in (source.name, str(source.id))
                                ),
                                field,
                            ),
                        )
                        for alias in rule.aliases
                        if alias.alias == field
                    ),
                    field,
                )
                if mapped in state.get("kusto_context_fields", ()):
                    return True
        return False

    def _group_value_identities(self, search, columns, allocate):
        source, row_id = allocate("_sigma_group_input"), allocate("_sigma_group_row")
        tuple_value = allocate("_sigma_group_tuple")
        groups = columns["groups"]
        values = columns.get("values", []) if columns.get("identify_values") else []
        valid_groups = " and ".join(f"isnotempty(tostring({group}[1]))" for group in groups)
        definitions = (
            f"let {source} = materialize({search}\n| where {valid_groups}\n"
            f"| extend {tuple_value} = pack_array(\"array\", pack_array({', '.join(groups)}))\n"
            f"| serialize {row_id} = row_number());\n"
        )
        expanded, component, boxed, expanded_id = [
            allocate(name)
            for name in (
                "_sigma_group_components",
                "_sigma_group_component",
                "_sigma_group_box",
                "_sigma_group_component_id",
            )
        ]
        definitions += (
            f"let {expanded} = materialize({source}\n"
            f"| mv-expand with_itemindex={component} {boxed} = pack_array({', '.join([tuple_value, *values])})\n"
            f"| where {component} == 0 or isnotempty(tostring({boxed}[1]))\n"
            f"| serialize {expanded_id} = row_number());\n"
        )
        fragment, relation, identity = self._dynamic_value_identity(expanded, expanded_id, boxed, [component], allocate)
        definitions += fragment
        cached = allocate("_sigma_group_identity_cache")
        definitions += f"let {cached} = materialize({expanded} | join kind=inner ({relation}) on {expanded_id}\n| project {row_id}, {component}, {identity});\n"
        group_identity = allocate("_sigma_group_key")
        normalized = f"{source}\n| join kind=inner ({cached} | where {component} == 0 | project {row_id}, {group_identity} = {identity}) on {row_id}\n"
        replacements = [f"{group} = {group_identity}" for group in groups]
        for index, value in enumerate(values, 1):
            value_identity = allocate("_sigma_group_value_identity")
            normalized += f"| join kind=leftouter ({cached} | where {component} == {index} | project {row_id}, {value_identity} = {identity}) on {row_id}\n"
            replacements.append(f"{value} = {value_identity}")
        normalized += "| extend " + ", ".join(replacements)
        if values:
            columns["values_preidentified"] = True
        lookup = allocate("_sigma_group_lookup")
        originals = [allocate("_sigma_group_original") for _ in groups]
        aggregates = ", ".join(f"{original} = take_any({group})" for original, group in zip(originals, groups))
        definitions += (
            f"let {lookup} = materialize({source} | join kind=inner ({cached} | where {component} == 0) on {row_id}\n"
            f"| summarize {aggregates} by {identity});\n"
        )
        lookups = [
            (field, lookup, identity, original) for field, original in zip(self._correlation_group_by, originals)
        ]
        return definitions, normalized, lookups

    def _retain_context(self, rule, queries, context_fields, metrics):
        """Attach context from the same source matches without changing metric inputs."""
        reserved = set(context_fields) | set(rule.group_by or [])
        reserved.update(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", "\n".join(queries)))

        def allocate(base):
            name, index = base, 0
            while name in reserved:
                index += 1
                name = f"{base}_{index}"
            reserved.add(name)
            return name

        columns = {
            "timestamp": allocate("_sigma_context_timestamp"),
            "rule": allocate("_sigma_context_rule"),
            "groups": [allocate("_sigma_context_group") for _ in (rule.group_by or [])],
            "values": [],
            "context": [allocate("_sigma_context_value") for _ in context_fields],
        }
        self._correlation_group_by = rule.group_by or []
        self._correlation_counted_fields = []
        self._correlation_context_fields = context_fields
        self._correlation_columns = columns
        self._correlation_typed_groups = bool(rule.group_by)
        try:
            definitions, search = self._count_search(rule, allocate)
        finally:
            del self._correlation_group_by, self._correlation_counted_fields
            del self._correlation_context_fields, self._correlation_columns
            del self._correlation_typed_groups
        summary_function, summaries, matched = [
            allocate(name) for name in ("_sigma_context_summary", "_sigma_context_summaries", "_sigma_context_matches")
        ]
        source_id, end, key = [
            allocate(name) for name in ("_sigma_context_id", "_sigma_context_end", "_sigma_context_key")
        ]
        relationships, count = [allocate(name) for name in ("_sigma_context_rows", "_sigma_context_count")]
        groups, timestamp, values = columns["groups"], columns["timestamp"], columns["context"]
        # A constant joins the single ungrouped partition; grouping never uses context values.
        join_key = allocate("_sigma_context_partition")
        group_projection = [
            f"{column} = {self._quote_correlation_field(field)}" for column, field in zip(groups, rule.group_by or [])
        ]
        left_projection = ", ".join([source_id, f"{end} = WindowEnd", *group_projection, f"{join_key} = 1"])
        right_projection = ", ".join([timestamp, columns["rule"], *groups, *values, f"{join_key} = 1"])
        span = rule.timespan.spec if rule.timespan.unit in "smhd" else f"{rule.timespan.seconds}s"
        join_fields = ", ".join(groups or [join_key])
        typed_groups = bool(rule.group_by)
        if typed_groups:
            group_projection = [
                f"{column} = pack_array(gettype({self._quote_correlation_field(field)}), {self._quote_correlation_field(field)})"
                for column, field in zip(groups, rule.group_by or [])
            ]
            left_projection = ", ".join([source_id, f"{end} = WindowEnd", *group_projection, f"{join_key} = 1"])
        output = []
        for query in queries:
            prefix = (
                f"let {summary_function} = () {{\n{query}\n}};\n"
                f"{definitions}let {summaries} = materialize({summary_function}()\n"
                f"| serialize {source_id} = row_number());\n"
            )
            if typed_groups:
                combined, normalized = allocate("_sigma_context_combined"), allocate("_sigma_context_normalized")
                prefix += f"let {combined} = union ({summaries} | project {left_projection}), ({search} | project {right_projection});\n"
                self._correlation_group_by = rule.group_by or []
                try:
                    fragment, shared_search, _ = self._group_value_identities(combined, columns, allocate)
                finally:
                    del self._correlation_group_by
                prefix += fragment + f"let {normalized} = materialize({shared_search});\n"
                left = f"{normalized} | where isnotnull({source_id})"
                right = f"{normalized} | where isnull({source_id}) | project {right_projection}"
            else:
                left = f"{summaries} | project {left_projection}"
                right = f"{search}\n| project {right_projection}"
            prefix += (
                f"let {matched} = materialize(\n{left}\n"
                f"| join kind=inner (\n{right}\n) on {join_fields}\n"
                f"| where {timestamp} between (({end} - {span}) .. {end})\n"
                f"| serialize {key} = row_number());\n"
            )
            if rule.type == SigmaCorrelationType.TEMPORAL_ORDERED:
                membership, matched = self._ordered_context_membership(
                    rule, matched, columns, source_id, end, key, allocate
                )
                prefix += membership
            if isinstance(rule.condition, SigmaExtendedCorrelationCondition):
                presence = {}
                aggregates = []
                for reference in rule.referenced_rules:
                    column = allocate("_sigma_context_present")
                    identity = json.dumps(str(reference.rule.name or reference.rule.id), ensure_ascii=False)
                    presence[reference.reference] = column
                    aggregates.append(f"{column} = countif({columns['rule']} == {identity}) > 0")
                eligible = allocate("_sigma_context_eligible")
                evidence = self._boolean_context_expression(rule, rule.condition.parsed, presence, columns["rule"])[1]
                prefix += (
                    f"let {eligible} = materialize({matched}\n"
                    f"| join kind=inner ({matched} | summarize {', '.join(aggregates)} by {source_id}) on {source_id}\n"
                    f"| where {evidence});\n"
                )
                context_matches = eligible
            else:
                context_matches = matched

            prefix += (
                f"let {relationships} = materialize({context_matches}\n"
                f"| summarize {relationships} = make_list(pack_array(pack_array({', '.join(values)})), 1048576), "
                f"{count} = count() by {source_id});\n"
            )
            collections = []
            for field, value in zip(context_fields, values):
                binding, collection, amount = [
                    allocate(name)
                    for name in ("_sigma_context_distinct", "_sigma_context_collection", "_sigma_context_amount")
                ]
                identity_definitions, identity_relation, identity = self._dynamic_value_identity(
                    context_matches, key, value, [source_id], allocate
                )
                prefix += identity_definitions
                prefix += (
                    f"let {binding} = {context_matches}\n| join kind=inner ({identity_relation}) on {key}\n"
                    f"| summarize {value} = take_any({value}) by {source_id}, {identity}\n"
                    f"| summarize {collection} = make_list(pack_array({value}[1]), 1048576), "
                    f"{amount} = count() by {source_id};\n"
                )
                collections.append((binding, collection, amount, field))
            self._register_correlation_guard(
                relationships, f"{count} != array_length({relationships})",
                "Kusto retained context collection limit exceeded",
            )
            result = (
                f"{summaries}\n| join kind=leftouter ({relationships}) on {source_id}\n"
                f"| extend {relationships} = coalesce({relationships}, dynamic([]))\n"
            )
            for binding, collection, amount, field in collections:
                self._register_correlation_guard(
                    binding, f"{amount} != array_length({collection})",
                    "Kusto retained context collection limit exceeded",
                )
                result += (
                    f"| join kind=leftouter ({binding}) on {source_id}\n"
                    f"| extend {self._quote_correlation_field(field)} = coalesce({collection}, dynamic([]))\n"
                )
            public = ["WindowStart", "WindowEnd", *(rule.group_by or []), *context_fields]
            result += "| project " + ", ".join(
                self._quote_correlation_field(field) for field in [*public, *metrics, relationships]
            )
            output.append(_CorrelationQuery(prefix, result))
        return output, relationships

    def _ordered_context_membership(self, rule, matches, columns, source_id, completion, key, allocate):
        """Union source rows from every full sequence for each qualifying completion.

        Unlike detection-only prefix pruning, evidence traversal keeps every
        alternative. Row identities preserve repeated matches and relationships
        without putting sequence alternatives into a bounded collection.
        """
        timestamp, rule_column = columns["timestamp"], columns["rule"]
        previous = allocate("_sigma_context_previous")
        paths = []
        row_ids = []
        definitions = ""
        for index, reference in enumerate(rule.referenced_rules):
            path = allocate("_sigma_context_path")
            row_id = allocate("_sigma_context_step")
            step_time = allocate("_sigma_context_step_time")
            identity = reference.rule.name or str(reference.rule.id)
            candidates = (
                f"{matches}\n| where {rule_column} == {json.dumps(identity, ensure_ascii=False)}\n"
                f"| project {source_id}, {completion}, {row_id} = {key}, {step_time} = {timestamp}"
            )
            if index == 0:
                expression = candidates
            else:
                expression = (
                    f"{paths[-1]}\n| join kind=inner (\n{candidates}\n) on {source_id}\n"
                    f"| where {step_time} > {previous}"
                )
            if index == len(rule.referenced_rules) - 1:
                expression += f"\n| where {step_time} == {completion}"
            row_ids.append(row_id)
            projection = ", ".join([source_id, completion, *row_ids, f"{previous} = {step_time}"])
            definitions += f"let {path} = {expression}\n| project {projection};\n"
            paths.append(path)
        participating = allocate("_sigma_context_participating")
        selected = allocate("_sigma_context_selected")
        alternatives = ", ".join(f"({paths[-1]} | project {source_id}, {key} = {row_id})" for row_id in row_ids)
        definitions += (
            f"let {participating} = union {alternatives}\n| distinct {source_id}, {key};\n"
            f"let {selected} = materialize({matches}\n"
            f"| join kind=leftsemi ({participating}) on {source_id}, {key});\n"
        )
        return definitions, selected

    @staticmethod
    def _correlation_source_tables(state):
        # These identities only reserve query-binding names. They never supply
        # a child's table, mappings, timestamp, or returned field schema.
        tables = set(state.processing_state.get("kusto_source_tables", ()))
        if table := state.processing_state.get("query_table"):
            tables.add(table)
        return tables

    @staticmethod
    def _validate_count_source(rule, source_rule):
        count_type = rule.type.name.lower()
        if source_rule is None:
            raise SigmaConversionError(rule, rule.source, f"Kusto {count_type} source rule reference is unresolved.")
        child = isinstance(source_rule, SigmaCorrelationRule)
        queries = source_rule.get_conversion_result()
        states = source_rule.get_conversion_states()
        if not queries or not states:
            raise SigmaConversionError(
                rule, rule.source, f"Kusto {count_type} correlations require a convertible source-rule filter."
            )
        if len(queries) != len(states):
            raise SigmaConversionError(
                rule, rule.source, f"Kusto {count_type} source query and conversion state counts do not match."
            )
        if child:
            schemas = [state.processing_state.get("kusto_result_fields") for state in states]
            timestamps = [state.processing_state.get("kusto_result_timestamp") for state in states]
            if not all(
                isinstance(fields, set)
                and all(isinstance(field, str) and field for field in fields)
                and isinstance(timestamp, str)
                and timestamp in fields
                and fields == schemas[0]
                and timestamp == timestamps[0]
                for fields, timestamp in zip(schemas, timestamps)
            ):
                raise SigmaConversionError(
                    rule,
                    rule.source,
                    "Kusto chaining requires consistent child result schema and timestamp metadata for every search.",
                )
        if not child and not all(state.processing_state.get("query_table") for state in states):
            raise SigmaConversionError(
                rule, rule.source, f"Kusto {count_type} correlations require a source rule with a mapped query table."
            )
        return queries, states

    def _count_search(self, rule, allocate):
        if not any(isinstance(reference.rule, SigmaCorrelationRule) for reference in rule.referenced_rules):
            return "", self.convert_correlation_search(rule)
        definitions, searches = [], []
        for reference_index, reference in enumerate(rule.referenced_rules):
            source = reference.rule
            self._correlation_child_row = allocate("_sigma_child_context_row")
            try:
                normalization = self.convert_correlation_search_field_normalization_expression(rule.aliases, reference)
            finally:
                del self._correlation_child_row
            for query_index, query in enumerate(source.get_conversion_result()):
                if isinstance(source, SigmaCorrelationRule):
                    state = source.get_conversion_states()[query_index].processing_state
                    query, child_checks, child_messages = self._child_raw_query(query, state)
                    if child_checks:
                        self._correlation_guard_state["messages"].update(child_messages)
                        self._correlation_guard_state["inherited"].update(child_checks)
                    shared = self._correlation_shared_children["enabled"]
                    if shared:
                        function, child_results = self._shared_child_source(reference_index, query_index, query)
                        relation = f"{child_results}{normalization}"
                    else:
                        function = allocate(f"_sigma_child_{len(definitions)}")
                        # Each function scopes the complete child query, including
                        # its let bindings. Only returned summaries feed the parent.
                        definitions.append(f"let {function} = () {{\n{query}\n}};\n")
                        child_results = f"{function}()"
                        relation = f"{child_results}{normalization}"
                    if normalization.expands_context and not shared:
                        child_results = allocate("_sigma_child_results")
                        definitions.append(f"let {child_results} = materialize({function}());\n")
                    filtered = child_results
                    if normalization.expands_context:
                        child_id = self._correlation_columns.get("child_match")
                        if child_id:
                            relation = (
                                f"{filtered}\n| serialize {child_id} = row_number()\n"
                                f'| extend {child_id} = strcat({json.dumps(function)}, "_", tostring({child_id}))'
                                f"{normalization.expansion}{normalization}"
                            )
                        else:
                            relation = f"{filtered}{normalization.expansion}{normalization}"
                    searches.append(
                        f"({relation}\n| extend {normalization.rule_column} = "
                        f"{json.dumps(str(source.name or source.id), ensure_ascii=False)})"
                    )
                else:
                    searches.append(
                        self.correlation_search_multi_rule_query_expression.format(
                            rule=source,
                            ruleid=self.convert_correlation_ruleid(
                                source.name or source.id, str(self.correlation_search_multi_rule_query_expression)
                            ),
                            query=query,
                            normalization=normalization,
                        )
                    )
        return "".join(definitions), "union " + self.correlation_search_multi_rule_query_expression_joiner.join(
            searches
        )

    def _convert_count(self, rule, timestamp_field, query_operator, counted_fields, metrics):
        ordered = rule.type == SigmaCorrelationType.TEMPORAL_ORDERED
        temporal = rule.type in (SigmaCorrelationType.TEMPORAL, SigmaCorrelationType.TEMPORAL_ORDERED)
        source_fields = {timestamp_field, *(rule.group_by or []), *(counted_fields or [])}
        for alias in rule.aliases:
            source_fields.add(alias.alias)
            source_fields.update(alias.mapping.values())
        for reference in rule.referenced_rules:
            _, states = self._validate_count_source(rule, getattr(reference, "rule", None))
            tables = [state.processing_state.get("query_table") for state in states]
            if len(set(tables)) != 1:
                raise SigmaConversionError(
                    rule,
                    rule.source,
                    f"Kusto {rule.type.name.lower()} searches for one source rule must use the same mapped query table.",
                )
            for state in states:
                source_fields.update(state.processing_state.get("kusto_source_fields", ()))
                source_fields.update(self._correlation_source_tables(state))

        boolean = isinstance(rule.condition, SigmaExtendedCorrelationCondition)
        threshold = rule.condition.count if rule.condition is not None and not boolean else len(rule.referenced_rules)
        original_correlation = rule.custom_attributes.get("correlation")
        omitted_condition = (
            rule.condition is None or original_correlation is not None and original_correlation.get("condition") is None
        )
        source_type_count = len({id(reference.rule) for reference in rule.referenced_rules})
        if temporal and omitted_condition:
            # pySigma supplies a default >= list length. Repeated references to
            # the same resolved source still represent only one rule type.
            threshold = source_type_count
        if ordered and not omitted_condition:
            equivalent_full_sequence = (
                query_operator in (">=", "==")
                and threshold == source_type_count
                or query_operator == ">"
                and threshold == source_type_count - 1
                or query_operator == "!="
                and source_type_count == 1
                and threshold == 0
            )
            if not equivalent_full_sequence:
                raise SigmaConversionError(
                    rule,
                    rule.source,
                    "Kusto temporal_ordered requires the full declared sequence; omit the condition or use "
                    f"gte: {source_type_count}, eq: {source_type_count}, or gt: {source_type_count - 1}. "
                    "Partial, subset, and unsatisfiable ordered thresholds are not supported.",
                )

        def allocate(name):
            candidate = name
            suffix = 0
            while candidate in source_fields:
                suffix += 1
                candidate = f"{name}_{suffix}"
            source_fields.add(candidate)
            return candidate

        columns = {
            "timestamp": allocate("_sigma_timestamp"),
            "rule": allocate("_sigma_rule"),
            "groups": [allocate(f"_sigma_group_{index}") for index in range(len(rule.group_by or []))],
            "weight": allocate("_sigma_weight"),
            "window_end": allocate("_sigma_window_end"),
            "bin": allocate("_sigma_bin"),
            "weights": allocate(
                "_sigma_types" if temporal else "_sigma_weights" if counted_fields is None else "_sigma_values"
            ),
        }
        numeric_context = rule.type in (
            SigmaCorrelationType.VALUE_SUM, SigmaCorrelationType.VALUE_AVG, SigmaCorrelationType.VALUE_PERCENTILE
        ) and self._uses_context_groups(rule, [*(rule.group_by or []), *(counted_fields or [])])
        if counted_fields is None and not temporal or numeric_context:
            columns["child_match"] = allocate("_sigma_child_match")
        if rule.type == SigmaCorrelationType.VALUE_COUNT:
            columns["identify_values"] = True
        if counted_fields is not None:
            columns["values"] = [
                allocate("_sigma_value" if len(counted_fields) == 1 else f"_sigma_value_{index}")
                for index in range(len(counted_fields))
            ]
        if rule.type == SigmaCorrelationType.VALUE_SUM:
            columns["integer_sum"] = allocate("_sigma_integer_sum")
            columns["real_sum"] = allocate("_sigma_real_sum")
            columns["real_count"] = allocate("_sigma_real_count")
        if rule.type == SigmaCorrelationType.VALUE_AVG:
            columns["numeric_count"] = allocate("_sigma_numeric_count")
        if ordered:
            columns["sequence_start"] = allocate("_sigma_sequence_start")
            columns["sequence_end"] = allocate("_sigma_sequence_end")
            columns["steps"] = [allocate(f"_sigma_sequence_{index}") for index in range(len(rule.referenced_rules))]
        self._correlation_group_by = rule.group_by or []
        self._correlation_counted_fields = counted_fields or []
        self._correlation_columns = columns
        try:
            definitions, search = self._count_search(rule, allocate)
            if getattr(self, "_correlation_typed_groups", False):
                group_definitions, search, self._correlation_group_lookups = self._group_value_identities(
                    search, columns, allocate
                )
                definitions += group_definitions
            if "child_match" in columns and (numeric_context or getattr(self, "_correlation_typed_groups", False)):
                source_matches = allocate("_sigma_parent_matches")
                child_id = columns["child_match"]
                definitions += f'let {source_matches} = materialize({search}\n| extend {child_id} = tostring(column_ifexists({json.dumps(child_id)}, "")));\n'
                child_matches = f"{source_matches} | where isnotempty({child_id})"
                keys = [child_id, *columns["groups"]]
                if numeric_context:
                    # Restriction to observed group/value relationships has already
                    # happened during expansion. Deduplicate only within each child
                    # and resulting group; direct observations retain multiplicity.
                    value = columns["values"][0]
                    kind = allocate("_sigma_numeric_kind")
                    precise = allocate("_sigma_numeric_decimal")
                    real = allocate("_sigma_numeric_real")
                    # Decimal preserves exact long/decimal distinctions; the real
                    # key preserves doubles beyond decimal's range and precision.
                    # Both keys plus runtime type identify numeric observations
                    # without serializing values or traversing arbitrary dynamics.
                    usable = (
                        f'tostring({value}[0]) in ("int", "long", "real", "double", "decimal") '
                        f"and isfinite(toreal({value}[1]))"
                    )
                    child_matches += (
                        f' | extend {kind} = iff({usable}, tostring({value}[0]), ""), '
                        f"{precise} = iff({usable}, todecimal({value}[1]), decimal(null)), "
                        f"{real} = iff({usable}, toreal({value}[1]), real(null))"
                    )
                    keys.extend([kind, precise, real])
                search = (
                    f"union ({source_matches} | where isempty({child_id})), "
                    f"({child_matches} | summarize take_any(*) by {', '.join(keys)})"
                )
        finally:
            del self._correlation_group_by
            del self._correlation_counted_fields
            del self._correlation_columns
        timestamp, weight, window_end, time_bin, weights = (
            columns[name] for name in ("timestamp", "weight", "window_end", "bin", "weights")
        )
        groups = columns["groups"]
        if len(metrics) > 1:
            metrics = [self._quote_correlation_field(metric) for metric in metrics]
        # Sigma's weeks, months, and years have fixed second lengths. Kusto
        # timespan literals do not accept those units.
        span = rule.timespan.spec if rule.timespan.unit in "smhd" else f"{rule.timespan.seconds}s"
        valid_rows = " and ".join([f"isnotnull({timestamp})", *(f"isnotempty({field})" for field in groups)])
        weighted_fields = ", ".join([timestamp, *groups])
        if ordered:
            return [
                _CorrelationQuery(definitions + query.definitions, query.body)
                for query in self._convert_ordered_temporal(rule, search, columns, valid_rows, span, source_type_count)
            ]
        anchor_fields = ", ".join([*groups, f"{window_end} = {timestamp}", f"{time_bin} = bin({timestamp}, {span})"])
        join_fields = ", ".join([*groups, time_bin])
        summary_fields = ", ".join([window_end, *groups])
        result_fields = self._correlation_result_projection(rule, groups, window_end, span, metrics)
        if rule.type == SigmaCorrelationType.VALUE_PERCENTILE:
            value = columns["values"][0]
            partition = allocate("_sigma_partition")
            rank = allocate("_sigma_rank")
            population = allocate("_sigma_population")
            numeric = allocate("_sigma_numeric")
            precise = allocate("_sigma_precise")
            ranked = allocate("_sigma_ranked")
            selector = rule.condition.percentile
            # Sort the complete observation stream, without capped arrays or an
            # approximate aggregate. The decimal tie-break distinguishes longs
            # that round to the same double; the real key covers double extremes
            # outside decimal's range. Preserve the selected observation's type.
            return [_CorrelationQuery(
                f"{definitions}let {weights} = {search}\n| where {valid_rows};\n"
                f"let {ranked} = materialize(\n"
                f"{weights}\n| project {anchor_fields}\n| distinct *\n"
                f"| join kind=inner (\n{weights}\n"
                f"| extend {time_bin} = pack_array(bin({timestamp}, {span}), bin({timestamp}, {span}) + {span})\n"
                f"| mv-expand {time_bin} to typeof(datetime)\n) on {join_fields}\n"
                f"| where {timestamp} between (({window_end} - {span}) .. {window_end})\n"
                f'| where tostring({value}[0]) in ("int", "long", "real", "double", "decimal") '
                f"and isfinite(toreal({value}[1]))\n"
                f"| extend {partition} = tostring(pack_array({summary_fields})), "
                f"{numeric} = toreal({value}[1]), {precise} = todecimal({value}[1])\n"
                f"| sort by {partition} asc, {numeric} asc, {precise} asc\n"
                f"| serialize {rank} = row_number(1, {partition} != prev({partition}))\n);\n",
                f"{ranked}\n| join kind=inner (\n"
                f"{ranked} | summarize {population} = max({rank}) by {partition}\n) on {partition}\n"
                # Quotient/remainder avoids multiplying an arbitrarily large
                # population by 100, and keeps the ceil operand below 10000.
                f"| where {rank} == max_of(1, ({population} / 100) * {selector} + "
                f"tolong(ceiling(toreal(({population} % 100) * {selector}) / 100.0)))\n"
                f"| extend ValuePercentile = {value}[1]\n"
                f'| where (gettype(ValuePercentile) in ("int", "long") and '
                f"todecimal(ValuePercentile) {query_operator} decimal({threshold})) or "
                f'(gettype(ValuePercentile) in ("real", "double", "decimal") and '
                f"toreal(ValuePercentile) {query_operator} {threshold})\n"
                f"| project {result_fields}"
            )]
        after_summary = ""
        summary_binding = ""
        if temporal:
            # One retained rule identity per timestamp/group is sufficient for
            # exact type counts; repetitions never become additional types.
            preaggregate = f"| summarize by {weighted_fields}, {columns['rule']}"
            distinct_anchors = "| distinct *\n"
            aggregates = [f"RuleCount = count_distinctif({columns['rule']}, isnotempty({columns['rule']}))"]
        elif counted_fields is None:
            preaggregate = f"| summarize {weight} = count() by {weighted_fields}"
            distinct_anchors = ""
            aggregates = [f"EventCount = sum({weight})"]
        elif rule.type == SigmaCorrelationType.VALUE_SUM:
            # All valid source matches remain in the stream, including repeated
            # measured values and rows without a usable value. The latter still
            # establish evaluation anchors.
            preaggregate = ""
            distinct_anchors = "| distinct *\n"
            value = columns["values"][0]
            value_type = f"tostring({value}[0])"
            integer = f'{value_type} in ("int", "long")'
            real = f'{value_type} in ("real", "double", "decimal") and isfinite(toreal({value}[1]))'
            integer_sum = columns["integer_sum"]
            real_sum = columns["real_sum"]
            real_count = columns["real_count"]
            aggregates = [
                f"{integer_sum} = sumif(todecimal({value}[1]), {integer})",
                f"{real_sum} = sumif(toreal({value}[1]), {real})",
                f"{real_count} = countif({real})",
            ]
            # Native long sum silently wraps on overflow. Accumulate integers
            # in decimal, check every anchored integer-only window before the
            # rule comparison, then expose the supported signed-long result.
            sums = allocate("_sigma_sums")
            summary_binding = f"let {sums} = materialize(\n"
            self._register_correlation_guard(
                sums,
                f"{real_count} == 0 and (isnull({integer_sum}) or "
                f"{integer_sum} < decimal(-9223372036854775808) or "
                f"{integer_sum} > decimal(9223372036854775807))",
                "Kusto value_sum integer overflow",
            )
            after_summary = (
                f"{sums}\n"
                f"| extend ValueSum = iff({real_count} > 0, "
                f"pack_array(toreal({integer_sum}) + {real_sum})[0], pack_array(tolong({integer_sum}))[0])\n"
            )
            comparisons = (
                f"({real_count} == 0 and todecimal({integer_sum}) {query_operator} decimal({threshold})) "
                f"or ({real_count} > 0 and toreal({integer_sum}) + {real_sum} {query_operator} {threshold})"
            )
        elif rule.type == SigmaCorrelationType.VALUE_AVG:
            # Preserve every observation and every valid anchor, including
            # anchors whose window contains no usable numeric observation.
            preaggregate = ""
            distinct_anchors = "| distinct *\n"
            value = columns["values"][0]
            numeric = (
                f'tostring({value}[0]) in ("int", "long", "real", "double", "decimal") '
                f"and isfinite(toreal({value}[1]))"
            )
            aggregates = [
                f"ValueAvg = avgif(toreal({value}[1]), {numeric})",
                f"{columns['numeric_count']} = countif({numeric})",
            ]
        else:
            definitions += self._counted_value_identities(search, columns, valid_rows, allocate)
            search = columns["identities"]
            # Scalar identities retain empty-value rows as anchors. Each field
            # is still counted independently across the entire window.
            preaggregate = f"| summarize by {weighted_fields}, {', '.join(columns['values'])}"
            distinct_anchors = "| distinct *\n"
            aggregates = [
                f"{metric} = count_distinctif({value}, isnotempty({value}))"
                for metric, value in zip(metrics, columns["values"])
            ]
        if boolean:
            references = {reference.reference: reference.rule for reference in rule.referenced_rules}
            presence = {}
            for reference, source in references.items():
                identity = json.dumps(str(source.name or source.id), ensure_ascii=False)
                column = allocate("_sigma_present")
                aggregates.append(f"{column} = countif({columns['rule']} == {identity}) > 0")
                presence[reference] = column
            comparisons = self._boolean_temporal_expression(rule, rule.condition.parsed, presence)
        elif rule.type != SigmaCorrelationType.VALUE_SUM:
            comparisons = " and ".join(f"{metric} {query_operator} {threshold}" for metric in metrics)
        if rule.type == SigmaCorrelationType.VALUE_AVG:
            # Kusto avgif() returns NaN for no observations, which satisfies
            # neq. Require an observation before applying any comparison.
            comparisons = f"{columns['numeric_count']} > 0 and {comparisons}"
        # Bins only prune candidate pairs; actual timestamps decide membership.
        scope = (
            f"{definitions}let {weights} = {search}\n"
            f"| where {valid_rows}\n{preaggregate};\n"
        )
        aggregate = (
            f"{weights}\n| project {anchor_fields}\n{distinct_anchors}"
            f"| join kind=inner (\n    {weights}\n"
            f"    | extend {time_bin} = pack_array(bin({timestamp}, {span}), bin({timestamp}, {span}) + {span})\n"
            f"    | mv-expand {time_bin} to typeof(datetime)\n) on {join_fields}\n"
            f"| where {timestamp} between (({window_end} - {span}) .. {window_end})\n"
            f"| summarize {', '.join(aggregates)} by {summary_fields}\n"
        )
        if summary_binding:
            scope += summary_binding + aggregate + ");\n"
            aggregate = after_summary
        return [_CorrelationQuery(scope, aggregate + f"| where {comparisons}\n| project {result_fields}")]

    def _boolean_temporal_expression(self, rule, node, presence):
        """Translate the upstream parsed tree over window-wide presence columns."""
        if isinstance(node, SigmaRuleReference) and node.reference in presence:
            return presence[node.reference]
        if isinstance(node, CorrelationConditionNOT) and len(node.args) == 1:
            return f"not({self._boolean_temporal_expression(rule, node.args[0], presence)})"
        if isinstance(node, (CorrelationConditionAND, CorrelationConditionOR)) and node.args:
            operator = " and " if isinstance(node, CorrelationConditionAND) else " or "
            return (
                "(" + operator.join(self._boolean_temporal_expression(rule, arg, presence) for arg in node.args) + ")"
            )
        raise SigmaConversionError(
            rule,
            rule.source,
            "Unsupported Boolean temporal expression; use resolved rule references, and, or, not, and parentheses.",
        )

    def _boolean_context_expression(self, rule, node, presence, source_column, negated=False):
        """Return truth and positive evidence predicates after pushing negation to leaves."""
        if isinstance(node, SigmaRuleReference):
            truth = presence[node.reference]
            if negated:
                return f"not({truth})", "false"
            source = next(ref.rule for ref in rule.referenced_rules if ref.reference == node.reference)
            identity = json.dumps(str(source.name or source.id), ensure_ascii=False)
            return truth, f"{source_column} == {identity}"
        if isinstance(node, CorrelationConditionNOT):
            return self._boolean_context_expression(rule, node.args[0], presence, source_column, not negated)
        children = [self._boolean_context_expression(rule, arg, presence, source_column, negated) for arg in node.args]
        conjunction = isinstance(node, CorrelationConditionAND) != negated
        truth = "(" + (" and " if conjunction else " or ").join(child[0] for child in children) + ")"
        evidence = (
            "("
            + " or ".join(f"({child_truth} and ({child_evidence}))" for child_truth, child_evidence in children)
            + ")"
        )
        return truth, f"({truth} and {evidence})" if conjunction else evidence

    def _dynamic_value_identity(self, source, row_id, value, groups, allocate):
        """Return an exact runtime-typed dynamic equivalence relation.

        ``value`` is boxed as [runtime type, dynamic value]. ``source`` must give
        stable globally unique long row IDs. Null/empty values are retained.
        """
        names = [
            "input",
            "steps",
            "relation",
            "bound",
            "iteration",
            "stack",
            "frame",
            "type",
            "path",
            "leaf",
            "terminal",
            "finished",
            "count",
            "canonical",
            "token",
            "tokens",
            "signature",
            "identity",
        ]
        n = {name: allocate("_sigma_identity_" + name) for name in names}
        stack, frame = n["stack"], n["frame"]
        old = f"s.{stack}"
        top = f"{old}[-1]"
        # Path components are prefix-distinguished and self-delimiting. JSON is
        # used for bag keys; runtime types are encoded separately for every leaf.
        parent = f"array_slice({old}, 0, -2)"
        updated = f"pack_array(bag_pack('v', {top}.v, 'p', {top}.p, 'i', tolong({top}.i) + 1, 'k', {top}.k))"
        key = f"{top}.k[tolong({top}.i)]"
        bag_child = f"pack_array(bag_pack('v', {top}.v[tostring({key})], 'p', strcat(tostring({top}.p), 'k', dynamic_to_json(pack_array({key}))), 'i', 0, 'k', bag_keys({top}.v[tostring({key})])))"
        array_child = f"pack_array(bag_pack('v', {top}.v[tolong({top}.i)], 'p', strcat(tostring({top}.p), 'a', tostring({top}.i), ';'), 'i', 0, 'k', bag_keys({top}.v[tolong({top}.i)])))"
        retained = ", ".join([row_id, *groups])
        # Each tree edge is descended and unwound once. JSON length exceeds
        # its node count, so this bound covers the whole walk without a depth
        # cap. Include the last available step even when the range is exhausted;
        # the final guard checks its bound and completion before returning IDs.
        bound, iteration, typ = n["bound"], n["iteration"], n["type"]
        definitions = (
            f"let {n['input']} = materialize({source}\n"
            f"| project {retained}, {n['leaf']} = {value}[1]\n"
            f"| extend {bound} = 3 * strlen(dynamic_to_json({n['leaf']})) + 3);\n"
            f"let {n['steps']} = materialize({n['input']}\n"
            f"| mv-expand {iteration} = range(0, min_of({bound}, 1048575), 1) to typeof(long)\n"
            f"| sort by {row_id} asc, {iteration} asc\n"
            f"| scan declare ({stack}:dynamic = dynamic([])) with (step s: true => {stack} = case(\n"
            f"    {iteration} == 0, pack_array(bag_pack('v', {n['leaf']}, 'p', '$', 'i', 0, 'k', bag_keys({n['leaf']}))),\n"
            f"    array_length({old}) == 0, {old},\n"
            f"    gettype({top}.v) == 'dictionary' and tolong({top}.i) < array_length({top}.k), array_concat({parent}, {updated}, {bag_child}),\n"
            f"    gettype({top}.v) == 'array' and tolong({top}.i) < array_length({top}.v), array_concat({parent}, {updated}, {array_child}),\n"
            f"    {parent});)\n"
            f"| extend {frame} = {stack}[-1]\n"
            f"| extend {typ} = gettype({frame}.v), {n['terminal']} = {iteration} == min_of({bound}, 1048575), {n['finished']} = array_length({stack}) == 0\n"
            f"| where {n['terminal']} or (not({n['finished']}) and ({typ} !in ('array', 'dictionary') or ({typ} == 'array' and array_length({frame}.v) == 0) or ({typ} == 'dictionary' and array_length(bag_keys({frame}.v)) == 0)))\n"
            f"| project {retained}, {n['path']} = tostring({frame}.p), {typ}, {n['leaf']} = {frame}.v, {n['terminal']}, {n['finished']}, {bound});\n"
        )
        # Serialize typed scalar leaves, never the original dynamic tree: JSON
        # alone loses extended runtime types. Full signatures avoid the leaf-pair
        # self-join whose repeated expansion overwhelms nested query planning.
        # Native scalar JSON preserves adjacent double/long precision. Normalize
        # equal signed zeros, NaNs and decimal scales before constructing tokens.
        leaf = n["leaf"]
        normalized = (
            f"case({typ} == 'double' and toreal({leaf}) == 0.0, '0', "
            f"{typ} == 'double' and isnan(toreal({leaf})), 'nan', "
            f"{typ} == 'decimal', tostring(todecimal({leaf})), dynamic_to_json({leaf}))"
        )
        ordered = ", ".join(f"{column} asc" for column in [*groups, n["signature"], row_id])
        same = " and ".join(f"{column} == s.{column}" for column in [*groups, n["signature"]])
        # Keep guards in this one summary: branching to independent scalar
        # checks earlier multiplies the source plan at every correlation level.
        # A scan assigns the minimum row ID without another canonical self-join.
        definitions += (
            f"let {n['canonical']} = materialize({n['steps']}\n"
            f"| extend {n['token']} = dynamic_to_json(pack_array({n['path']}, {typ}, {normalized}))\n"
            f"| sort by {row_id} asc, {n['path']} asc\n"
            f"| summarize {n['tokens']} = make_list_if({n['token']}, not({n['terminal']}), 1048576), {n['count']} = countif(not({n['terminal']})), {n['finished']} = countif({n['terminal']} and (not({n['finished']}) or {bound} > 1048575)) by {retained}\n"
            f"| extend {n['signature']} = dynamic_to_json({n['tokens']}));\n"
            f"let {n['relation']} = {n['canonical']}\n"
            f"| sort by {ordered}\n"
            f"| scan declare ({n['identity']}:long = 0) with (step s: true => {n['identity']} = iff({same}, s.{n['identity']}, {row_id});)\n"
            f"| project {row_id}, {n['identity']};\n"
        )
        self._register_correlation_guard(
            n["canonical"],
            f"{n['count']} != array_length({n['tokens']}) or {n['finished']} != 0",
            "Kusto dynamic identity traversal incomplete or exceeds range limit; signature leaf limit exceeded",
        )
        return definitions, n["relation"], n["identity"]

    def _counted_value_identities(self, search, columns, valid_rows, allocate):
        if columns.get("values_preidentified"):
            identities = columns["identities"] = allocate("_sigma_value_identities")
            return f"let {identities} = materialize({search}\n| where {valid_rows});\n"
        # Materialize once so every field uses the same source-match IDs.
        source = allocate("_sigma_value_input")
        identities = columns["identities"] = allocate("_sigma_value_identities")
        row_id = allocate("_sigma_value_id")
        values, groups = columns["values"], columns["groups"]
        retained = ", ".join([columns["timestamp"], *groups, *values])
        definitions = (
            f"let {source} = materialize({search}\n| where {valid_rows}\n"
            f"| project {retained}\n| serialize {row_id} = row_number());\n"
        )
        joins, replacements = [], []
        for value in values:
            present = allocate("_sigma_present_values")
            definitions += f"let {present} = {source} | where isnotempty(tostring({value}[1]));\n"
            fragment, relation, identity = self._dynamic_value_identity(present, row_id, value, groups, allocate)
            definitions += fragment
            joins.append(f"| join kind=leftouter ({relation}) on {row_id}\n")
            replacements.append(f"{value} = {identity}")
        definitions += (
            f"let {identities} = {source}\n"
            + "".join(joins)
            + f"| project {columns['timestamp']}, "
            + ", ".join([*groups, *replacements])
            + ";\n"
        )
        return definitions

    def _convert_ordered_temporal(self, rule, search, columns, valid_rows, span, source_type_count):
        timestamp, rule_column, types, start, end, time_bin = (
            columns[name] for name in ("timestamp", "rule", "weights", "sequence_start", "sequence_end", "bin")
        )
        groups, steps = columns["groups"], columns["steps"]
        identities = [reference.rule.name or str(reference.rule.id) for reference in rule.referenced_rules]
        retained_fields = ", ".join([timestamp, *groups, rule_column])
        first_fields = ", ".join([f"{start} = {timestamp}", f"{end} = {timestamp}", *groups])
        candidate_fields = ", ".join([timestamp, *groups, f"{time_bin} = bin({timestamp}, {span})"])
        join_fields = ", ".join([*groups, time_bin])
        summary_fields = ", ".join([f"{end} = {timestamp}", *groups])
        query = (
            f"let {types} = {search}\n| where {valid_rows}\n| summarize by {retained_fields};\n"
            f"let {steps[0]} = {types}\n"
            f"| where {rule_column} == {json.dumps(identities[0], ensure_ascii=False)}\n"
            f"| project {first_fields};\n"
        )
        for index, identity in enumerate(identities[1:], 1):
            # Each prefix ending at one timestamp needs only its latest viable
            # start. Any earlier start has the same future ordering constraints
            # and a tighter expiry, so cannot enable an additional completion.
            query += (
                f"let {steps[index]} = {steps[index - 1]}\n"
                f"| extend {time_bin} = pack_array(bin({start}, {span}), bin({start}, {span}) + {span})\n"
                f"| mv-expand {time_bin} to typeof(datetime)\n"
                f"| join kind=inner (\n"
                f"    {types}\n"
                f"    | where {rule_column} == {json.dumps(identity, ensure_ascii=False)}\n"
                f"    | project {candidate_fields}\n"
                f") on {join_fields}\n"
                f"| where {timestamp} > {end} and {timestamp} <= {start} + {span}\n"
                f"| summarize {start} = max({start}) by {summary_fields};\n"
            )
        result_fields = self._correlation_result_projection(
            rule, groups, end, span, [f"RuleCount = tolong({source_type_count})"]
        )
        return [_CorrelationQuery(query, f"{steps[-1]}\n| project {result_fields}")]

    def _correlation_result_projection(self, rule, groups, window_end, span, metrics):
        return ", ".join(
            [
                f"WindowStart = {window_end} - {span}",
                f"WindowEnd = {window_end}",
                *(
                    f"{self._quote_correlation_field(field)} = {column}"
                    for field, column in zip(rule.group_by or [], groups)
                ),
                *metrics,
            ]
        )

    def convert_correlation_search_field_normalization_expression(self, aliases, rule_reference, source_state=None):
        source_state = source_state or rule_reference.rule.get_conversion_states()[0]
        child = isinstance(rule_reference.rule, SigmaCorrelationRule)
        table = source_state.processing_state.get("query_table")
        mappings = source_state.processing_state.get("kusto_field_mappings")

        def map_field(field):
            if child:
                returned_fields = source_state.processing_state["kusto_result_fields"]
                if field not in returned_fields:
                    source = rule_reference.rule
                    raise SigmaConversionError(
                        source,
                        source.source,
                        f"Kusto child correlation {source.name or source.id!r} does not return required field "
                        f"{field!r}. Available fields: {', '.join(sorted(returned_fields))}.",
                    )
                return field
            return mappings.get_field_mapping(table, field) if mappings is not None else field

        def map_alias(field):
            if field not in aliases.aliases:
                return field
            alias = aliases.aliases[field]
            # A name and UUID may resolve to the same source rule even though
            # pySigma's references compare by their original spelling.
            return alias.mapping.get(
                rule_reference,
                next(
                    (
                        name
                        for reference, name in alias.mapping.items()
                        if reference.reference in (rule_reference.rule.name, str(rule_reference.rule.id))
                    ),
                    field,
                ),
            )

        timestamp_field = (
            source_state.processing_state["kusto_result_timestamp"]
            if child
            else map_field(self.backend_options["correlation_timestamp_field"])
        )
        columns = self._correlation_columns
        fields = [f"{columns['timestamp']} = todatetime({self._quote_correlation_field(timestamp_field)})"]
        context_fields = source_state.processing_state.get("kusto_context_fields", ()) if child else ()
        row_column = getattr(self, "_correlation_child_row", "_sigma_child_context_row")
        expanded = False

        def boxed(field):
            nonlocal expanded
            if field in context_fields:
                expanded = True
                return f"{row_column}[{context_fields.index(field)}]"
            quoted = self._quote_correlation_field(field)
            return f"pack_array(gettype({quoted}), {quoted})"

        for index, field in enumerate(self._correlation_group_by):
            mapped_field = map_field(map_alias(field))
            if getattr(self, "_correlation_typed_groups", False):
                fields.append(f"{columns['groups'][index]} = {boxed(mapped_field)}")
            else:
                fields.append(f"{columns['groups'][index]} = tostring({self._quote_correlation_field(mapped_field)})")
        for index, counted_field in enumerate(self._correlation_counted_fields):
            mapped_field = map_field(map_alias(counted_field))
            fields.append(f"{columns['values'][index]} = {boxed(mapped_field)}")
        for field, column in zip(getattr(self, "_correlation_context_fields", []), columns.get("context", [])):
            mapped_field = map_field(map_alias(field))
            fields.append(f"{column} = {boxed(mapped_field)}")
        result = _CorrelationNormalization("\n| extend " + ", ".join(fields), columns["rule"])
        result.expands_context = expanded
        result.expansion = (
            f"\n| mv-expand {row_column} = "
            f"{self._quote_correlation_field(source_state.processing_state['kusto_context_rows'])}"
            if expanded
            else ""
        )
        return result

    @staticmethod
    def _quote_correlation_field(field):
        return "[" + json.dumps(field, ensure_ascii=False) + "]"
