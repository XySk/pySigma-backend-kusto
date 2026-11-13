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
from sigma.correlations import SigmaCorrelationConditionOperator, SigmaCorrelationRule, SigmaCorrelationType
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
            ruleid=json.dumps(str(ruleid), ensure_ascii=False),
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

    def convert_correlation_rule(self, rule, output_format=None, method=None):
        converters = {
            SigmaCorrelationType.EVENT_COUNT: self.convert_correlation_event_count_rule,
            SigmaCorrelationType.VALUE_COUNT: self.convert_correlation_value_count_rule,
            SigmaCorrelationType.TEMPORAL: self.convert_correlation_temporal_rule,
            SigmaCorrelationType.TEMPORAL_ORDERED: self.convert_correlation_temporal_ordered_rule,
        }
        if rule.type not in converters:
            raise SigmaConversionError(
                rule,
                rule.source,
                "Kusto supports only event_count, value_count, temporal, and temporal_ordered correlation rules.",
            )
        queries = converters[rule.type](rule, output_format, method or "default")
        rule.set_conversion_result(queries)
        rule.set_conversion_states(
            [ConversionState(processing_state=dict(self.last_processing_pipeline.state)) for _ in queries]
        )
        return queries

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

    def _convert_count_rule(self, rule, counted_fields=None):
        count_type = rule.type.name.lower()
        if rule.type in (SigmaCorrelationType.TEMPORAL, SigmaCorrelationType.TEMPORAL_ORDERED):
            metrics = ["RuleCount"]
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
        if not rule.rules:
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
        query_operator = condition_operators.get(rule.condition.op) if rule.condition is not None else ">="
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

        return self._convert_count(rule, timestamp_field, query_operator, counted_fields, metrics)

    @staticmethod
    def _validate_count_source(rule, source_rule):
        count_type = rule.type.name.lower()
        if source_rule is None:
            raise SigmaConversionError(rule, rule.source, f"Kusto {count_type} source rule reference is unresolved.")
        if isinstance(source_rule, SigmaCorrelationRule):
            raise SigmaConversionError(rule, rule.source, "Chained correlation sources are not yet supported by Kusto.")
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
        if not all(state.processing_state.get("query_table") for state in states):
            raise SigmaConversionError(
                rule, rule.source, f"Kusto {count_type} correlations require a source rule with a mapped query table."
            )
        return queries, states

    def _convert_count(self, rule, timestamp_field, query_operator, counted_fields, metrics):
        ordered = rule.type == SigmaCorrelationType.TEMPORAL_ORDERED
        temporal = rule.type in (SigmaCorrelationType.TEMPORAL, SigmaCorrelationType.TEMPORAL_ORDERED)
        source_fields = {timestamp_field, *(rule.group_by or []), *(counted_fields or [])}
        for alias in rule.aliases:
            source_fields.add(alias.alias)
            source_fields.update(alias.mapping.values())
        for reference in rule.rules:
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

        threshold = rule.condition.count if rule.condition is not None else len(rule.rules)
        original_correlation = rule.custom_attributes.get("correlation")
        omitted_condition = (
            rule.condition is None or original_correlation is not None and original_correlation.get("condition") is None
        )
        source_type_count = len({id(reference.rule) for reference in rule.rules})
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
        if counted_fields is not None:
            columns["values"] = [
                allocate("_sigma_value" if len(counted_fields) == 1 else f"_sigma_value_{index}")
                for index in range(len(counted_fields))
            ]
        if ordered:
            columns["sequence_start"] = allocate("_sigma_sequence_start")
            columns["sequence_end"] = allocate("_sigma_sequence_end")
            columns["steps"] = [allocate(f"_sigma_sequence_{index}") for index in range(len(rule.rules))]
        self._correlation_group_by = rule.group_by or []
        self._correlation_counted_fields = counted_fields or []
        self._correlation_columns = columns
        try:
            search = self.convert_correlation_search(rule)
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
            return self._convert_ordered_temporal(rule, search, columns, valid_rows, span, source_type_count)
        anchor_fields = ", ".join([*groups, f"{window_end} = {timestamp}", f"{time_bin} = bin({timestamp}, {span})"])
        join_fields = ", ".join([*groups, time_bin])
        summary_fields = ", ".join([window_end, *groups])
        result_fields = ", ".join(
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
        else:
            # Retain values, including empty ones that still establish anchors.
            # Deduplicating complete rows here does not determine cardinality:
            # each field is counted independently across the entire window.
            preaggregate = f"| summarize by {weighted_fields}, {', '.join(columns['values'])}"
            distinct_anchors = "| distinct *\n"
            aggregates = [
                f"{metric} = count_distinctif({value}, isnotempty({value}))"
                for metric, value in zip(metrics, columns["values"])
            ]
        comparisons = " and ".join(f"{metric} {query_operator} {threshold}" for metric in metrics)
        # Bins only prune candidate pairs: a contribution belongs to its own bin
        # and the following anchor bin. Actual timestamps determine membership.
        return [
            f"let {weights} = {search}\n"
            f"| where {valid_rows}\n"
            f"{preaggregate};\n"
            f"{weights}\n"
            f"| project {anchor_fields}\n"
            f"{distinct_anchors}"
            f"| join kind=inner (\n"
            f"    {weights}\n"
            f"    | extend {time_bin} = pack_array(bin({timestamp}, {span}), bin({timestamp}, {span}) + {span})\n"
            f"    | mv-expand {time_bin} to typeof(datetime)\n"
            f") on {join_fields}\n"
            f"| where {timestamp} between (({window_end} - {span}) .. {window_end})\n"
            f"| summarize {', '.join(aggregates)} by {summary_fields}\n"
            f"| where {comparisons}\n"
            f"| project {result_fields}"
        ]

    def _convert_ordered_temporal(self, rule, search, columns, valid_rows, span, source_type_count):
        timestamp, rule_column, types, start, end, time_bin = (
            columns[name] for name in ("timestamp", "rule", "weights", "sequence_start", "sequence_end", "bin")
        )
        groups, steps = columns["groups"], columns["steps"]
        identities = [reference.rule.name or str(reference.rule.id) for reference in rule.rules]
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
        result_fields = ", ".join(
            [
                f"WindowStart = {end} - {span}",
                f"WindowEnd = {end}",
                *(
                    self._quote_correlation_field(field) + f" = {column}"
                    for field, column in zip(rule.group_by or [], groups)
                ),
                f"RuleCount = tolong({source_type_count})",
            ]
        )
        return [query + f"{steps[-1]}\n| project {result_fields}"]

    def convert_correlation_search_field_normalization_expression(self, aliases, rule_reference):
        source_state = rule_reference.rule.get_conversion_states()[0]
        table = source_state.processing_state["query_table"]
        mappings = source_state.processing_state.get("kusto_field_mappings")

        def map_field(field):
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
                    (name for reference, name in alias.mapping.items() if reference.rule is rule_reference.rule), field
                ),
            )

        timestamp_field = map_field(self.backend_options["correlation_timestamp_field"])
        columns = self._correlation_columns
        fields = [f"{columns['timestamp']} = todatetime({self._quote_correlation_field(timestamp_field)})"]
        for index, field in enumerate(self._correlation_group_by):
            mapped_field = map_field(map_alias(field))
            fields.append(f"{columns['groups'][index]} = tostring({self._quote_correlation_field(mapped_field)})")
        for index, counted_field in enumerate(self._correlation_counted_fields):
            mapped_field = map_field(map_alias(counted_field))
            fields.append(f"{columns['values'][index]} = tostring({self._quote_correlation_field(mapped_field)})")
        return _CorrelationNormalization("\n| extend " + ", ".join(fields), columns["rule"])

    @staticmethod
    def _quote_correlation_field(field):
        return "[" + json.dumps(field, ensure_ascii=False) + "]"
