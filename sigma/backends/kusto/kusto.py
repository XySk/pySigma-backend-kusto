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
from sigma.processing.transformations.base import FieldMappingTransformationBase
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

    def escape_and_quote_field(self, field_name):
        if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", field_name):
            return field_name
        return self._quote_correlation_field(field_name)

    def convert_rule(self, rule, output_format=None, callback=None):
        requested_fields = list(rule.fields or [])
        queries = super().convert_rule(rule, output_format, callback)
        if self.collect_errors and self.errors and self.errors[-1][0] is rule:
            return queries
        for state in rule.get_conversion_states():
            state.processing_state["kusto_requested_fields"] = requested_fields.copy()
        return queries

    def convert_correlation_ruleid(self, ruleid, template):
        return json.dumps(str(ruleid), ensure_ascii=False)

    def finalize_query(self, rule, query, index, state, output_format):
        # Pipeline state is reset for the next source rule. Keep the mapping used
        # for this search with its conversion state for later normalization.
        state.processing_state["kusto_field_mappings"] = deepcopy(
            self.last_processing_pipeline.vars.get("kusto_field_mappings")
        )
        # Keep an independent source snapshot: pySigma resets tracking and
        # source-specific transformations when processing the next rule.
        pipeline = deepcopy(
            self.last_processing_pipeline,
            {
                id(
                    self.last_processing_pipeline.resolve_regex_engine()
                ): self.last_processing_pipeline.resolve_regex_engine()
            },
        )
        state.processing_state["kusto_standard_field_mappings"] = deepcopy(pipeline.field_mappings)
        state.processing_state["kusto_field_transformations"] = [
            item
            for item, applied in zip(pipeline.items, pipeline.applied)
            if applied and isinstance(item.transformation, FieldMappingTransformationBase)
        ]
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
        for item in state.processing_state.get("kusto_field_transformations", ()):
            mapping = getattr(item.transformation, "mapping", {})
            fields.update(field for field in mapping if field is not None)
            for target in mapping.values():
                if target is not None:
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

        queries = self._convert_count(rule, timestamp_field, query_operator, counted_fields, metrics)
        requested_context = list(rule.fields or [])
        for reference in rule.referenced_rules:
            source = reference.rule
            requested_fields = (
                source.get_conversion_states()[0].processing_state.get("kusto_requested_fields", source.fields)
                if source.get_conversion_states()
                else source.fields
            )
            for field in requested_fields or []:
                logical = next(
                    (
                        alias.alias
                        for alias in rule.aliases
                        if any(
                            name == field and alias_reference.reference in (source.name, str(source.id))
                            for alias_reference, name in alias.mapping.items()
                        )
                    ),
                    field,
                )
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
        context_fields = [field for field in context_fields if field not in (rule.group_by or [])]
        context_column = None
        if context_fields:
            queries, context_column = self._retain_context(rule, queries, context_fields, metrics)
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
                        "kusto_context_fields": tuple(context_fields),
                        "kusto_context_rows": context_column,
                        "kusto_source_tables": source_tables.copy(),
                    }
                )
                for _ in queries
            ]
        )
        return queries

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
        try:
            definitions, search = self._count_search(rule, allocate)
        finally:
            del self._correlation_group_by, self._correlation_counted_fields
            del self._correlation_context_fields, self._correlation_columns
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
        output = []
        for query in queries:
            prefix = (
                f"let {summary_function} = () {{\n{query}\n}};\n"
                f"{definitions}let {summaries} = materialize({summary_function}()\n"
                f"| serialize {source_id} = row_number());\n"
                f"let {matched} = materialize(\n{summaries} | project {left_projection}\n"
                f"| join kind=inner (\n{search}\n| project {right_projection}\n) on {join_fields}\n"
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
            result = (
                f"{summaries}\n| join kind=leftouter ({relationships}) on {source_id}\n"
                f"| extend {relationships} = coalesce({relationships}, dynamic([]))\n"
                f"| where assert(toscalar({relationships} | summarize n = countif({count} != "
                f"array_length({relationships})) | project n == 0), "
                f'"Kusto retained context collection limit exceeded")\n'
            )
            for binding, collection, amount, field in collections:
                result += (
                    f"| join kind=leftouter ({binding}) on {source_id}\n"
                    f"| where assert(toscalar({binding} | summarize n = countif({amount} != "
                    f"array_length({collection})) | project n == 0), "
                    f'"Kusto retained context collection limit exceeded")\n'
                    f"| extend {self._quote_correlation_field(field)} = coalesce({collection}, dynamic([]))\n"
                )
            public = ["WindowStart", "WindowEnd", *(rule.group_by or []), *context_fields]
            result += "| project " + ", ".join(
                self._quote_correlation_field(field) for field in [*public, *metrics, relationships]
            )
            output.append(prefix + result)
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
        for reference in rule.referenced_rules:
            source = reference.rule
            normalization = self.convert_correlation_search_field_normalization_expression(rule.aliases, reference)
            for query in source.get_conversion_result():
                if isinstance(source, SigmaCorrelationRule):
                    function = allocate(f"_sigma_child_{len(definitions)}")
                    # Each function scopes the complete child query, including
                    # its let bindings. Only returned summaries feed the parent.
                    definitions.append(f"let {function} = () {{\n{query}\n}};\n")
                    searches.append(
                        f"({function}(){normalization}\n| extend {normalization.rule_column} = "
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
                definitions + query
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
            return [
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
                f"| serialize {rank} = row_number(1, {partition} != prev({partition}))\n);\n"
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
            ]
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
            valid = allocate("_sigma_sum_valid")
            summary_binding = f"let {sums} = materialize(\n"
            after_summary = (
                f");\n{sums}\n"
                f"| where assert(toscalar({sums} | summarize {valid} = countif("
                f"{real_count} == 0 and (isnull({integer_sum}) or "
                f"{integer_sum} < decimal(-9223372036854775808) or "
                f"{integer_sum} > decimal(9223372036854775807))) == 0 | project {valid}), "
                f'"Kusto value_sum integer overflow")\n'
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
        # Bins only prune candidate pairs: a contribution belongs to its own bin
        # and the following anchor bin. Actual timestamps determine membership.
        return [
            f"{definitions}let {weights} = {search}\n"
            f"| where {valid_rows}\n"
            f"{preaggregate};\n"
            f"{summary_binding}"
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
            f"{after_summary}"
            f"| where {comparisons}\n"
            f"| project {result_fields}"
        ]

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
            "leaves",
            "counts",
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
            "other_id",
            "other_leaf",
            "count",
            "other_count",
            "hits",
            "identity",
        ]
        n = {name: allocate("_sigma_identity_" + name) for name in names}
        stack, frame = n["stack"], n["frame"]
        old = f"s.{stack}"
        top = f"{old}[-1]"
        # Path components are prefix-distinguished and self-delimiting. JSON is
        # used only for string bag keys, never for identifying runtime values.
        parent = f"array_slice({old}, 0, -2)"
        updated = f"pack_array(bag_pack('v', {top}.v, 'p', {top}.p, 'i', tolong({top}.i) + 1, 'k', {top}.k))"
        key = f"{top}.k[tolong({top}.i)]"
        bag_child = f"pack_array(bag_pack('v', {top}.v[tostring({key})], 'p', strcat(tostring({top}.p), 'k', dynamic_to_json(pack_array({key}))), 'i', 0, 'k', bag_keys({top}.v[tostring({key})])))"
        array_child = f"pack_array(bag_pack('v', {top}.v[tolong({top}.i)], 'p', strcat(tostring({top}.p), 'a', tostring({top}.i), ';'), 'i', 0, 'k', bag_keys({top}.v[tolong({top}.i)])))"
        retained = ", ".join([row_id, *groups])
        # Each tree edge is descended and unwound once. JSON length exceeds
        # its node count, so this bound covers the whole walk without a depth
        # cap. Check range() capacity first, then check terminal completion.
        bound, iteration, typ = n["bound"], n["iteration"], n["type"]
        definitions = (
            f"let {n['input']} = materialize({source}\n"
            f"| project {retained}, {n['leaf']} = {value}[1]\n"
            f"| extend {bound} = 3 * strlen(dynamic_to_json({n['leaf']})) + 3);\n"
            f"let {n['steps']} = materialize({n['input']}\n"
            f"| where assert(toscalar({n['input']} | summarize coalesce(max({bound}), 0)) <= 1048575, 'Kusto dynamic identity traversal exceeds range limit')\n"
            f"| mv-expand {iteration} = range(0, {bound}, 1) to typeof(long)\n"
            f"| sort by {row_id} asc, {iteration} asc\n"
            f"| scan declare ({stack}:dynamic = dynamic([])) with (step s: true => {stack} = case(\n"
            f"    {iteration} == 0, pack_array(bag_pack('v', {n['leaf']}, 'p', '$', 'i', 0, 'k', bag_keys({n['leaf']}))),\n"
            f"    array_length({old}) == 0, {old},\n"
            f"    gettype({top}.v) == 'dictionary' and tolong({top}.i) < array_length({top}.k), array_concat({parent}, {updated}, {bag_child}),\n"
            f"    gettype({top}.v) == 'array' and tolong({top}.i) < array_length({top}.v), array_concat({parent}, {updated}, {array_child}),\n"
            f"    {parent});)\n"
            f"| extend {frame} = {stack}[-1]\n"
            f"| extend {typ} = gettype({frame}.v), {n['terminal']} = {iteration} == {bound}, {n['finished']} = array_length({stack}) == 0\n"
            f"| where {n['terminal']} or (not({n['finished']}) and ({typ} !in ('array', 'dictionary') or ({typ} == 'array' and array_length({frame}.v) == 0) or ({typ} == 'dictionary' and array_length(bag_keys({frame}.v)) == 0)))\n"
            f"| project {retained}, {n['path']} = tostring({frame}.p), {typ}, {n['leaf']} = {frame}.v, {n['terminal']}, {n['finished']});\n"
            f"let {n['leaves']} = {n['steps']}\n"
            f"| where assert(toscalar({n['steps']} | summarize countif({n['terminal']} and not({n['finished']}))) == 0, 'Kusto dynamic identity traversal incomplete')\n"
            f"| where not({n['terminal']});\n"
            f"let {n['counts']} = {n['leaves']} | summarize {n['count']} = count() by {row_id};\n"
        )
        other_fields = ", ".join(
            [*groups, n["path"], typ, f"{n['other_id']} = {row_id}", f"{n['other_leaf']} = {n['leaf']}"]
        )
        keys = ", ".join([*groups, n["path"], typ])
        leaf, other = n["leaf"], n["other_leaf"]
        equality = (
            f"case({typ} == 'double', (toreal({leaf}) == toreal({other}) or (isnan(toreal({leaf})) and isnan(toreal({other})))), "
            f"{typ} == 'long', tolong({leaf}) == tolong({other}), "
            f"{typ} == 'decimal', todecimal({leaf}) == todecimal({other}), "
            f"tostring({leaf}) == tostring({other}))"
        )
        definitions += (
            f"let {n['relation']} = {n['leaves']}\n"
            f"| join kind=inner ({n['leaves']} | project {other_fields}) on {keys}\n"
            f"| where {equality}\n"
            f"| summarize {n['hits']} = count() by {row_id}, {n['other_id']}\n"
            f"| join kind=inner ({n['counts']}) on {row_id}\n"
            f"| join kind=inner ({n['counts']} | project {n['other_id']} = {row_id}, {n['other_count']} = {n['count']}) on {n['other_id']}\n"
            f"| where {n['hits']} == {n['count']} and {n['hits']} == {n['other_count']}\n"
            f"| summarize {n['identity']} = min({n['other_id']}) by {row_id};\n"
        )
        return definitions, n["relation"], n["identity"]

    def _counted_value_identities(self, search, columns, valid_rows, allocate):
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
        return [query + f"{steps[-1]}\n| project {result_fields}"]

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

    @staticmethod
    def _correlation_mapping_targets(value):
        # FieldMappings.get_field_mapping historically selects the first entry;
        # correlations need the complete target set to diagnose ambiguity.
        return value if isinstance(value, list) else [value]

    def _map_correlation_source_field(self, source, state, field):
        """Resolve one logical column using this source's applied pipeline."""
        tracked = state.processing_state.get("kusto_standard_field_mappings", {})
        mappings = state.processing_state.get("kusto_field_mappings")
        transformations = state.processing_state.get("kusto_field_transformations", ())
        table = state.processing_state.get("query_table")
        if field in tracked:
            targets = set(tracked[field])
        else:
            targets = {field}
            builtin_seen = False
            # Tracking excludes fields absent from detection predicates. Replay
            # source-applicable field-name transformations in their original order.
            for item in transformations:
                transformation = item.transformation
                if type(transformation).__module__.startswith("sigma.pipelines.kusto_common") and mappings:
                    if builtin_seen:
                        continue
                    builtin_seen = True
                    targets = {
                        mapped
                        for target in targets
                        for mapped in self._correlation_mapping_targets(
                            mappings.table_mappings.get(table, {}).get(
                                target, mappings.generic_mappings.get(target, target)
                            )
                        )
                    }
                else:
                    targets = {mapped for target in targets for mapped in transformation._apply_field_name(target)}
            # Pipelines may provide built-in mapping metadata directly without
            # using the built-in transformation classes.
            if mappings and not builtin_seen:
                targets = {
                    mapped
                    for target in targets
                    for mapped in self._correlation_mapping_targets(
                        mappings.table_mappings.get(table, {}).get(
                            target, mappings.generic_mappings.get(target, target)
                        )
                    )
                }
        if len(targets) != 1:
            raise SigmaConversionError(
                source,
                source.source,
                f"Kusto correlation field {field!r} in source {source.name or source.id!r} "
                f"must resolve to one column; mapped targets: {', '.join(sorted(targets)) or '(none)'}. "
                "For example, User -> [ActorUsername, TargetUsername] is ambiguous. "
                "Use a source-specific one-column mapping such as User -> ActorUsername, "
                "an explicit alias to the intended physical field, or normalize both columns "
                "to one field in the pipeline.",
            )
        return next(iter(targets))

    def convert_correlation_search_field_normalization_expression(self, aliases, rule_reference):
        source_state = rule_reference.rule.get_conversion_states()[0]
        child = isinstance(rule_reference.rule, SigmaCorrelationRule)

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
            return self._map_correlation_source_field(rule_reference.rule, source_state, field)

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
        for index, field in enumerate(self._correlation_group_by):
            mapped_field = map_field(map_alias(field))
            fields.append(f"{columns['groups'][index]} = tostring({self._quote_correlation_field(mapped_field)})")
        for index, counted_field in enumerate(self._correlation_counted_fields):
            mapped_field = map_field(map_alias(counted_field))
            quoted = self._quote_correlation_field(mapped_field)
            fields.append(f"{columns['values'][index]} = pack_array(gettype({quoted}), {quoted})")
        for field, column in zip(getattr(self, "_correlation_context_fields", []), columns.get("context", [])):
            mapped_field = map_field(map_alias(field))
            quoted = self._quote_correlation_field(mapped_field)
            fields.append(f"{column} = pack_array(gettype({quoted}), {quoted})")
        return _CorrelationNormalization("\n| extend " + ", ".join(fields), columns["rule"])

    @staticmethod
    def _quote_correlation_field(field):
        return "[" + json.dumps(field, ensure_ascii=False) + "]"
