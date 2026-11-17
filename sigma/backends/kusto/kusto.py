import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, ClassVar, Dict, Pattern, Tuple, Type, Union

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


class _CorrelationQuery(str):
    """A generated declaration scope and its final data expression."""

    def __new__(cls, definitions, body):
        instance = super().__new__(cls, definitions + body)
        instance.definitions, instance.body = definitions, body
        return instance

    def __add__(self, suffix):
        result = type(self)(self.definitions, self.body + suffix)
        if hasattr(self, "context_artifact"):
            result.context_artifact = self.context_artifact
        return result


@dataclass(frozen=True)
class _PreparedCorrelationInput:
    """Validated source relation and shared condition for a correlation strategy."""

    rule: SigmaCorrelationRule
    definitions: str
    search: str
    columns: dict
    span: str
    query_operator: str
    threshold: int | float
    allocate: Callable[[str], str]


@dataclass(frozen=True)
class _CorrelationRelationBinding:
    """A named correlation relation with an explicit evaluation policy."""

    name: str
    expression: str
    consumer_count: int = 1
    completed_reductions: tuple[str, ...] = ()
    preserve_evaluation: bool = False
    measured_materialization: bool = False

    def render(self):
        if self.consumer_count < 1:
            raise ValueError("correlation relation bindings require at least one consumer")
        cache = self.consumer_count > 1 or self.preserve_evaluation or self.measured_materialization
        if cache:
            return f"let {self.name} = materialize({self.expression});\n"
        separator = "" if self.expression.startswith("\n") else " "
        return f"let {self.name} ={separator}{self.expression};\n"


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

    def convert(self, rule_collection, output_format=None, correlation_method=None, callback=None):
        # Resolve the ordinary collection once before planning so generation
        # controls and every shared/multilevel consumer are visible together.
        rule_collection.resolve_rule_references()
        self._correlation_graph = self._prepare_correlation_graph(rule_collection, callback)
        try:
            self._correlation_reporting = self._reporting_options()
            return super().convert(rule_collection, output_format, correlation_method, callback)
        finally:
            del self._correlation_graph
            self.__dict__.pop("_correlation_reporting", None)

    def _prepare_correlation_graph(self, rule_collection, callback=None):
        """Collect source/consumer requirements without a second rule parser."""
        consumers = {}
        context = {}
        for rule in rule_collection.rules:
            inherited = set(rule.fields or [])
            if isinstance(rule, SigmaCorrelationRule):
                for reference in rule.referenced_rules:
                    source = reference.rule
                    consumers.setdefault(id(source), []).append((rule, reference))
                    for field in context.get(id(source), ()):
                        logical = next(
                            (
                                alias.alias
                                for alias in rule.aliases
                                if any(
                                    name == field and alias_source.reference in (source.name, str(source.id))
                                    for alias_source, name in alias.mapping.items()
                                )
                            ),
                            field,
                        )
                        inherited.add(logical)
            context[id(rule)] = inherited
        existence = set()
        for child in rule_collection.rules:
            if (
                not isinstance(child, SigmaCorrelationRule)
                or child.type != SigmaCorrelationType.EVENT_COUNT
                or child._output
                or context[id(child)]
                or callback is not None
                or not consumers.get(id(child))
            ):
                continue
            available = {"WindowStart", "WindowEnd", *(child.group_by or [])}
            for consumer, reference in consumers[id(child)]:
                # Context inherited from another source can also request this
                # child's metric through a consumer alias.
                required = [*(consumer.group_by or []), *context[id(consumer)]]
                measured = getattr(consumer.condition, "fieldref", None)
                required.extend(measured if isinstance(measured, list) else [measured] if measured else [])
                for field in required:
                    if field in consumer.aliases.aliases:
                        alias = consumer.aliases.aliases[field]
                        field = next(
                            (
                                name
                                for source, name in alias.mapping.items()
                                if source.reference in (reference.reference, child.name, str(child.id))
                            ),
                            field,
                        )
                    if field not in available:
                        break
                else:
                    continue
                break
            else:
                existence.add(id(child))
        lookback, visiting = {}, set()

        def required_history(rule):
            if id(rule) in visiting:
                raise SigmaConversionError(rule, rule.source, "Kusto correlation dependencies must be acyclic.")
            if id(rule) not in lookback:
                visiting.add(id(rule))
                ancestors = [required_history(parent) for parent, _ in consumers.get(id(rule), ())]
                own = rule.timespan.seconds if isinstance(rule, SigmaCorrelationRule) else 0
                lookback[id(rule)] = own + max(ancestors, default=0)
                visiting.remove(id(rule))
            return lookback[id(rule)]

        for rule in rule_collection.rules:
            required_history(rule)
        return {"consumers": consumers, "context": context, "existence_only": existence, "lookback": lookback}

    def _reporting_options(self):
        bounds = {}
        comparable = {}
        for bound in ("start", "end"):
            name = "correlation_report_" + bound
            value = self.backend_options.get(name)
            if value is None:
                continue
            match = (
                re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,7}))?(Z|[+-]\d{2}:\d{2})", value)
                if isinstance(value, str)
                else None
            )
            try:
                if not match:
                    raise ValueError()
                fraction = (match[2] or "").ljust(7, "0")
                parsed = datetime.fromisoformat(match[1] + "." + fraction[:6] + match[3]).astimezone(timezone.utc)
                bounds[bound] = parsed.isoformat(timespec="seconds")[:-6] + "." + fraction + "Z"
                comparable[bound] = (parsed, fraction[6])
            except (ValueError, OverflowError):
                raise SigmaConversionError(
                    None,
                    None,
                    f"{name} must be an ISO 8601 datetime with timezone and at most seven fractional digits; "
                    "for example '2026-01-01T10:00:00.0000001Z'.",
                ) from None
        if "start" in bounds and "end" in bounds and comparable["start"] >= comparable["end"]:
            raise SigmaConversionError(
                None, None, "correlation_report_start must be earlier than correlation_report_end."
            )
        return bounds

    def _count_existence_threshold(self, rule):
        if id(rule) not in getattr(self, "_correlation_graph", {}).get("existence_only", ()):
            return None
        condition = rule.condition
        if condition is None or isinstance(condition, SigmaExtendedCorrelationCondition):
            return None
        count = condition.count
        if count != int(count):
            return None
        if condition.op == SigmaCorrelationConditionOperator.GTE:
            threshold = int(count)
        elif condition.op == SigmaCorrelationConditionOperator.GT:
            threshold = int(count) + 1
        else:
            return None
        # Verified engine SEM0248 restricts prev() offsets to [1..1024].
        # Threshold one needs no offset; larger unsupported thresholds use
        # exact general counts instead of producing an invalid query.
        return threshold if 0 < threshold <= 1025 else None

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
        state.processing_state["kusto_schema"] = deepcopy(self.last_processing_pipeline.vars.get("kusto_schema"))
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
                new_cond = ConditionFieldEqualsValueExpression(field=cond.args[0].field, value=arg)
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
        if method is not None and (not isinstance(method, str) or method not in self.correlation_methods):
            raise SigmaConversionError(
                rule,
                rule.source,
                f"Unsupported Kusto correlation method {method!r}; use correlation_method='default'.",
            )
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
        limit = self.backend_options.get("correlation_context_limit", 1000)
        if type(limit) is not int or not 1 <= limit <= 1048576:
            raise SigmaConversionError(
                rule, rule.source, "correlation_context_limit must be an integer from 1 through 1048576."
            )
        timestamp_field = self.backend_options.get("correlation_timestamp_field")
        if not isinstance(timestamp_field, str) or not timestamp_field.strip():
            raise SigmaConversionError(
                rule,
                rule.source,
                "The correlation_timestamp_field backend option is required as a nonempty field-name string; "
                "for example correlation_timestamp_field='Timestamp'.",
            )
        for reference in rule.referenced_rules:
            self._validate_count_source(rule, getattr(reference, "rule", None))
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
        children = [
            reference.rule for reference in rule.referenced_rules if isinstance(reference.rule, SigmaCorrelationRule)
        ]
        enabled = bool(children) and bool(
            rule.fields
            or any(
                source.fields
                or any(
                    state.processing_state.get("kusto_context_fields")
                    or state.processing_state.get("kusto_requested_fields")
                    for state in source.get_conversion_states() or []
                )
                for source in children
            )
        )
        report_names = {}
        report_definitions = ""
        for bound, value in getattr(self, "_correlation_reporting", {}).items():
            name = "Report" + bound.title()
            while name in reserved:
                name += "_"
            reserved.add(name)
            report_names[bound] = name
            report_definitions += f"let {name} = datetime({value});\n"
        self._correlation_report_names = report_names
        self._correlation_report_lookback = (
            getattr(self, "_correlation_graph", {}).get("lookback", {}).get(id(rule), rule.timespan.seconds)
        )
        shared = {"reserved": reserved, "definitions": [], "bindings": {}, "enabled": enabled}
        self._correlation_shared_children = shared
        try:
            queries = self._build_count_rule(rule, counted_fields)
            prefix = report_definitions + "".join(shared["definitions"])
            raw_queries = [prefix + query for query in queries]
            output = []
            for raw, state in zip(raw_queries, rule.get_conversion_states()):
                artifact = state.processing_state.get("kusto_context_artifact")
                if artifact:
                    artifact = dict(artifact, query=prefix + artifact["query"])
                    state.processing_state["kusto_context_artifact"] = artifact
                report_conditions = []
                if "start" in report_names:
                    report_conditions.append(f"WindowEnd >= {report_names['start']}")
                if "end" in report_names:
                    report_conditions.append(f"WindowEnd < {report_names['end']}")
                report_suffix = "\n| where " + " and ".join(report_conditions) if report_conditions else ""
                public = raw + report_suffix
                state.processing_state.update(
                    {
                        "kusto_report_suffix": report_suffix,
                        "kusto_raw_query": raw,
                        "kusto_public_query": public,
                    }
                )
                if artifact:
                    artifact["public_query"] = public
                output.append(public)
            return output
        finally:
            del self._correlation_shared_children
            del self._correlation_report_names, self._correlation_report_lookback

    @staticmethod
    def _child_raw_query(query, state, source):
        """Replay appended callback edits against complete, unreported history."""
        raw, public, suffix = (
            state.get(key) for key in ("kusto_raw_query", "kusto_public_query", "kusto_report_suffix")
        )
        if not (
            isinstance(raw, str)
            and raw
            and isinstance(public, str)
            and public
            and isinstance(suffix, str)
            and public == raw + suffix
            and query.startswith(public)
        ):
            raise SigmaConversionError(
                source,
                source.source,
                f"Kusto child correlation {source.name or source.id or source.title!r} query and provenance metadata "
                "are inconsistent; preserve the generated public query and its "
                "kusto_raw_query/kusto_public_query/kusto_report_suffix metadata when using conversion callbacks.",
            )
        return raw + query[len(public) :]

    @staticmethod
    def _child_context_artifact(state, source):
        """Require complete relationship provenance before accessing retained fields."""
        artifact = state.get("kusto_context_artifact")
        context = state.get("kusto_context_fields", ())
        if not context and artifact is None:
            return None
        if not (
            isinstance(context, (tuple, list))
            and all(isinstance(field, str) and field for field in context)
            and isinstance(artifact, dict)
            and all(
                isinstance(artifact.get(key), str) and artifact[key] for key in ("query", "id", "kind", "public_query")
            )
            and artifact["public_query"] == state.get("kusto_public_query")
            and isinstance(artifact.get("fields"), dict)
            and set(artifact["fields"]) == set(context)
            and all(isinstance(value, str) and value for value in artifact["fields"].values())
            and len(set([artifact["id"], artifact["kind"], *artifact["fields"].values()])) == len(context) + 2
        ):
            raise SigmaConversionError(
                source,
                source.source,
                "Kusto child relational context metadata is inconsistent; preserve the generated public query "
                "and complete kusto_context_artifact conversion state.",
            )
        return artifact

    @staticmethod
    def _name_allocator(reserved):
        """Allocate unique helper identifiers within the caller's reserved scope."""

        def allocate(base):
            candidate, suffix = base, 0
            while candidate in reserved:
                suffix += 1
                candidate = f"{base}_{suffix}"
            reserved.add(candidate)
            return candidate

        return allocate

    @staticmethod
    def _child_binding_label(source):
        label = "".join(
            part[:1].upper() + part[1:]
            for part in re.findall(r"[A-Za-z0-9]+", str(source.name or source.id or source.title))
        )
        return label if label and not label[0].isdigit() else "ChildMatches"

    def _shared_child_source(self, source, reference_index, query_index, query):
        shared = self._correlation_shared_children
        key = (reference_index, query_index, query)
        if key not in shared["bindings"]:

            allocate = self._name_allocator(shared["reserved"])

            label = self._child_binding_label(source)
            function, results = allocate(label), allocate(label + "Results")
            shared["definitions"].extend(
                [
                    f"let {function} = () {{\n{query}\n}};\n",
                    _CorrelationRelationBinding(
                        results,
                        f"{function}()",
                        preserve_evaluation=True,
                        completed_reductions=("preserve the child public summary/evidence schema",),
                    ).render(),
                ]
            )
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
            metrics = [] if self._count_existence_threshold(rule) is not None else ["EventCount"]
        elif len(counted_fields) == 1:
            metrics = ["ValueCount"]
        else:
            metrics = [f"ValueCount_{field}" for field in counted_fields]
        if rule.timespan.seconds <= 0:
            raise SigmaConversionError(rule, rule.source, "Kusto correlations require a positive timespan.")
        conflicting_fields = {"WindowStart", "WindowEnd", "ContextComplete", *metrics}.intersection(rule.group_by or [])
        if rule.type == SigmaCorrelationType.EVENT_COUNT and "EventCount" in (rule.group_by or []):
            conflicting_fields.add("EventCount")
        if conflicting_fields:
            raise SigmaConversionError(
                rule,
                rule.source,
                f"Kusto {count_type} grouping fields conflict with result columns: "
                + ", ".join(sorted(conflicting_fields))
                + ". Use an explicit correlation alias for the source field.",
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
        if not isinstance(timestamp_field, str) or not timestamp_field.strip():
            raise SigmaConversionError(
                rule,
                rule.source,
                f"The correlation_timestamp_field backend option is required as a nonempty field-name string "
                f"for {count_type} correlations; for example correlation_timestamp_field='Timestamp'.",
            )

        self._correlation_field_types = {}
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
        conflicts = {"WindowStart", "WindowEnd", "ContextComplete", *metrics}.intersection(context_fields)
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
        if context_fields:
            queries = self._retain_context(rule, queries, context_fields, metrics)
        elif requested_fields:
            queries = [query + "\n| extend ContextComplete = true" for query in queries]
        result_fields = {"WindowStart", "WindowEnd", *(rule.group_by or []), *metrics, *context_fields}
        if requested_fields:
            result_fields.add("ContextComplete")
        source_tables = set().union(
            *(
                self._correlation_source_tables(state)
                for reference in rule.referenced_rules
                for state in reference.rule.get_conversion_states()
            )
        )
        result_types = {
            "WindowStart": "datetime",
            "WindowEnd": "datetime",
            **{field: self._correlation_field_types[field] for field in [*(rule.group_by or []), *context_fields]},
        }
        if requested_fields:
            result_types["ContextComplete"] = "bool"
        for metric in metrics:
            if rule.type == SigmaCorrelationType.VALUE_AVG:
                result_types[metric] = "real"
            elif rule.type == SigmaCorrelationType.VALUE_SUM:
                result_types[metric] = (
                    "long"
                    if self._correlation_field_types[counted_fields[0]] in ("int", "long")
                    else self._correlation_field_types[counted_fields[0]]
                )
            elif rule.type == SigmaCorrelationType.VALUE_PERCENTILE:
                result_types[metric] = self._correlation_field_types[counted_fields[0]]
            else:
                result_types[metric] = "long"
        # Summaries expose their result columns, independent of source tables
        # and the pipeline mappings that produced their underlying events.
        rule.set_conversion_states(
            [
                ConversionState(
                    processing_state={
                        "kusto_result_fields": result_fields.copy(),
                        "kusto_result_types": result_types.copy(),
                        "kusto_source_fields": result_fields.copy(),
                        "kusto_result_timestamp": "WindowEnd",
                        "kusto_requested_fields": requested_fields,
                        "kusto_context_fields": tuple(context_fields),
                        "kusto_context_artifact": getattr(query, "context_artifact", None),
                        "kusto_source_tables": source_tables.copy(),
                    }
                )
                for query in queries
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

    def _retain_context(self, rule, queries, context_fields, metrics):
        """Attach context from the same source matches without changing metric inputs."""
        reserved = set(context_fields) | set(rule.group_by or [])
        reserved.update(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", "\n".join(queries)))
        for reference in rule.referenced_rules:
            for state in reference.rule.get_conversion_states():
                reserved.update(state.processing_state.get("kusto_source_fields", ()))
                reserved.update(self._correlation_source_tables(state))

        allocate = self._name_allocator(reserved)

        columns = {
            "timestamp": allocate("ContextEventTime"),
            "rule": allocate("ContextSourceRule"),
            "groups": [
                allocate("ContextGroup" + re.sub(r"[^A-Za-z0-9_]", "_", field)) for field in (rule.group_by or [])
            ],
            "values": [],
            "context": [allocate("Context" + re.sub(r"[^A-Za-z0-9_]", "_", field)) for field in context_fields],
        }
        if self._uses_context_groups(rule, context_fields):
            columns["context_evidence"] = allocate("HasContextEvidence")
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
            allocate(name) for name in ("ContextSummary", "CorrelationMatches", "WindowSourceMatches")
        ]
        source_id, end, key = [
            allocate(name) for name in ("CorrelationMatchId", "CompletionTime", "SourceRelationshipId")
        ]
        relationships = allocate("CompleteContextRelationships")
        groups, timestamp, values = columns["groups"], columns["timestamp"], columns["context"]
        # A constant joins the single ungrouped partition; grouping never uses context values.
        join_key = allocate("ContextPartition")
        group_projection = [
            f"{column} = {self._quote_correlation_field(field)}" for column, field in zip(groups, rule.group_by or [])
        ]
        left_projection = ", ".join([source_id, f"{end} = WindowEnd", *group_projection, f"{join_key} = 1"])
        evidence = [columns["context_evidence"]] if "context_evidence" in columns else []
        right_projection = ", ".join([timestamp, columns["rule"], *groups, *values, *evidence, f"{join_key} = 1"])
        span = rule.timespan.spec if rule.timespan.unit in "smhd" else f"{rule.timespan.seconds}s"
        join_fields = ", ".join(groups or [join_key])
        output = []
        for query in queries:
            prefix = f"let {summary_function} = () {{\n{query}\n}};\n{definitions}"
            prefix += _CorrelationRelationBinding(
                summaries,
                f"{summary_function}()\n| serialize {source_id} = row_number()",
                preserve_evaluation=True,
                completed_reductions=("project correlation summary rows", "assign stable match identity"),
            ).render()
            left = f"{summaries} | project {left_projection}"
            right = f"{search}\n| project {right_projection}"
            relationship_key = (
                f"\n| serialize {key} = row_number()"
                if rule.type == SigmaCorrelationType.TEMPORAL_ORDERED and len(rule.referenced_rules) > 2
                else ""
            )
            matched_expression = (
                f"\n{left}\n| join kind=inner (\n{right}\n) on {join_fields}\n"
                f"| where {timestamp} between (({end} - {span}) .. {end}){relationship_key}"
            )
            prefix += _CorrelationRelationBinding(
                matched,
                matched_expression,
                consumer_count=(
                    2
                    if isinstance(rule.condition, SigmaExtendedCorrelationCondition)
                    else (
                        len(rule.referenced_rules) + 1
                        if rule.type == SigmaCorrelationType.TEMPORAL_ORDERED and len(rule.referenced_rules) > 2
                        else 1
                    )
                ),
                completed_reductions=("restrict source matches to the exact correlation window",),
                preserve_evaluation=bool(relationship_key),
                measured_materialization=rule.type
                in (
                    SigmaCorrelationType.VALUE_COUNT,
                    SigmaCorrelationType.VALUE_AVG,
                    SigmaCorrelationType.VALUE_PERCENTILE,
                ),
            ).render()
            if rule.type == SigmaCorrelationType.TEMPORAL_ORDERED:
                membership, matched = self._ordered_context_membership(
                    rule, matched, columns, source_id, end, key, allocate
                )
                prefix += membership
            if isinstance(rule.condition, SigmaExtendedCorrelationCondition):
                presence = {}
                aggregates = []
                for reference in rule.referenced_rules:
                    column = allocate(
                        "Has" + re.sub(r"[^A-Za-z0-9_]", "_", str(reference.rule.name or reference.rule.id))
                    )
                    identity = json.dumps(str(reference.rule.name or reference.rule.id), ensure_ascii=False)
                    presence[reference.reference] = column
                    aggregates.append(f"{column} = countif({columns['rule']} == {identity}) > 0")
                eligible = allocate("BooleanParticipants")
                evidence = self._boolean_context_expression(rule, rule.condition.parsed, presence, columns["rule"])[1]
                prefix += _CorrelationRelationBinding(
                    eligible,
                    f"{matched}\n"
                    f"| join kind=inner ({matched} | summarize {', '.join(aggregates)} by {source_id}) on {source_id}\n"
                    f"| where {evidence}",
                    completed_reductions=("retain only positive participants in satisfied Boolean alternatives",),
                ).render()
                context_matches = eligible
            else:
                context_matches = matched

            # Complete native scalar relationship rows are a separate internal relation.
            # The display limit applies only to the public per-field collections.
            relationship_fields = ", ".join([source_id, *values])
            relationship_source = context_matches
            if "context_evidence" in columns:
                relationship_source += f"\n| where {columns['context_evidence']}"
            has_parent_consumer = bool(getattr(self, "_correlation_graph", {}).get("consumers", {}).get(id(rule)))
            prefix += _CorrelationRelationBinding(
                relationships,
                f"{relationship_source}\n| project {relationship_fields}\n| distinct *",
                consumer_count=len(context_fields) + int(has_parent_consumer),
                completed_reductions=(
                    "project exact retained relationship identity",
                    "deduplicate exact relationship tuples",
                ),
                measured_materialization=rule.type == SigmaCorrelationType.VALUE_AVG,
            ).render()
            artifact_prefix = prefix
            collections = []
            limit = self.backend_options.get("correlation_context_limit", 1000)
            for field, value in zip(context_fields, values):
                binding, collection, amount = [
                    allocate(re.sub(r"[^A-Za-z0-9_]", "_", name))
                    for name in ("ValuesFor" + field, "Displayed" + field, "Distinct" + field + "Count")
                ]
                prefix += (
                    f"let {binding} = {relationships}\n| where isnotnull({value})\n"
                    f"| summarize by {source_id}, {value}\n"
                    f"| summarize {collection} = make_list({value}, {limit}), {amount} = count() by {source_id};\n"
                )
                collections.append((binding, collection, amount, field))
            result = f"{summaries}\n"
            for binding, collection, amount, field in collections:
                result += (
                    f"| join kind=leftouter ({binding}) on {source_id}\n"
                    f"| extend {self._quote_correlation_field(field)} = coalesce({collection}, dynamic([]))\n"
                )
            complete = " and ".join(f"coalesce({amount}, 0) <= {limit}" for _, _, amount, _ in collections)
            public = ["WindowStart", "WindowEnd", *(rule.group_by or []), *context_fields, *metrics, "ContextComplete"]
            result += f"| extend ContextComplete = {complete}\n| project " + ", ".join(
                self._quote_correlation_field(field) for field in public
            )
            generated = _CorrelationQuery(prefix, result)
            # Both branches share one materialized match-ID assignment. Evidence
            # rows retain observed tuples, including nulls, without a list cap.
            row_kind = allocate("ContextRowKind")
            generated.context_artifact = {
                "query": artifact_prefix + f'union ({summaries} | extend {row_kind} = "Summary"), '
                f"({summaries} | join kind=inner ({relationships}) on {source_id} "
                f'| extend {row_kind} = "ContextEvidence")',
                "kind": row_kind,
                "id": source_id,
                "fields": dict(zip(context_fields, values)),
            }
            output.append(generated)
        return output

    def _ordered_context_membership(self, rule, matches, columns, source_id, completion, key, allocate):
        """Find all sequence participants with scalar prefix/suffix reachability.

        Candidates already lie in one successful completion's inclusive window.
        The earliest reachable prefix end and latest reachable suffix start are
        the least restrictive witnesses on either side of a candidate. Joining
        those scalar witnesses never enumerates combinations of alternatives.
        """
        timestamp, rule_column = columns["timestamp"], columns["rule"]
        # Successful summaries guarantee a viable full sequence. Every final
        # source row at the completion can replace its final witness, and the
        # final suffix bound is therefore the completion itself. Structural
        # child summaries preserve this guarantee even with no retained values.
        if len(rule.referenced_rules) <= 2:
            selected = allocate("OrderedSourceRelationships")
            predicates = []
            for index, reference in enumerate(rule.referenced_rules):
                identity = json.dumps(str(reference.rule.name or reference.rule.id), ensure_ascii=False)
                comparison = "==" if index == len(rule.referenced_rules) - 1 else "<"
                predicates.append(f"({rule_column} == {identity} and {timestamp} {comparison} {completion})")
            return (
                "// Successful one/two-step completions need only endpoint participant filters.\n"
                + _CorrelationRelationBinding(
                    selected,
                    f"{matches}\n| where {' or '.join(predicates)}",
                    completed_reductions=("retain only successful ordered endpoint participants",),
                ).render(),
                selected,
            )

        candidates, labels = [], []
        definitions = (
            "// All candidates share one successful completion's inclusive window.\n"
            "// Strict prefix/suffix witnesses retain every participating alternative.\n"
        )
        for reference in rule.referenced_rules:
            identity = str(reference.rule.name or reference.rule.id)
            label = re.sub(r"[^A-Za-z0-9_]", "_", identity)
            relation = allocate("CandidatesFor" + label)
            definitions += (
                f"let {relation} = {matches}\n"
                f"| where {rule_column} == {json.dumps(identity, ensure_ascii=False)}\n"
                f"| project {source_id}, {completion}, {key}, {timestamp};\n"
            )
            labels.append(label)
            candidates.append(relation)

        # Every threshold relation contains at most one row per match ID.
        earliest, latest = [], [None] * len(candidates)
        for index in range(len(candidates) - 2):
            relation, threshold = allocate("Earliest" + labels[index] + "Prefix"), allocate("PrefixEndTime")
            expression = candidates[index]
            if index:
                prior, bound = earliest[-1]
                expression += f"\n| join kind=inner ({prior}) on {source_id}" f"\n| where {timestamp} > {bound}"
            definitions += _CorrelationRelationBinding(
                relation,
                f"{expression}\n| summarize {threshold} = min({timestamp}) by {source_id}",
                consumer_count=1 + int(index < len(candidates) - 3),
                completed_reductions=("reduce reachable prefixes to the earliest witness per match",),
            ).render()
            earliest.append((relation, threshold))

        for index in range(len(candidates) - 2, 0, -1):
            relation, threshold = allocate("Latest" + labels[index] + "Suffix"), allocate("SuffixStartTime")
            expression = candidates[index]
            if index == len(candidates) - 2:
                expression += f"\n| where {timestamp} < {completion}"
            else:
                following, bound = latest[index + 1]
                expression += f"\n| join kind=inner ({following}) on {source_id}" f"\n| where {timestamp} < {bound}"
            definitions += _CorrelationRelationBinding(
                relation,
                f"{expression}\n| summarize {threshold} = max({timestamp}) by {source_id}",
                consumer_count=1 + int(index > 1),
                completed_reductions=("reduce reachable suffixes to the latest witness per match",),
            ).render()
            latest[index] = (relation, threshold)

        participants = []
        for index, candidate in enumerate(candidates):
            expression = candidate
            if index and index < len(candidates) - 1:
                prior, bound = earliest[index - 1]
                expression += f"\n| join kind=inner ({prior}) on {source_id}" f"\n| where {timestamp} > {bound}"
            if index == len(candidates) - 1:
                expression += f"\n| where {timestamp} == {completion}"
            elif index == len(candidates) - 2:
                expression += f"\n| where {timestamp} < {completion}"
            else:
                following, bound = latest[index + 1]
                expression += f"\n| join kind=inner ({following}) on {source_id}" f"\n| where {timestamp} < {bound}"
            participants.append(f"({expression}\n| project {source_id}, {key})")
        participating = allocate("OrderedParticipants")
        selected = allocate("OrderedSourceRelationships")
        definitions += f"let {participating} = union {', '.join(participants)}\n| distinct {source_id}, {key};\n"
        definitions += _CorrelationRelationBinding(
            selected,
            f"{matches}\n| join kind=leftsemi ({participating}) on {source_id}, {key}",
            completed_reductions=("retain only source relationships reachable from a successful ordered sequence",),
        ).render()
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
        identity = source_rule.name or source_rule.id or source_rule.title

        def fail(message):
            raise SigmaConversionError(rule, rule.source, f"Kusto source {identity!r}: {message}")

        try:
            queries = source_rule.get_conversion_result()
            states = source_rule.get_conversion_states()
        except SigmaConversionError as error:
            raise SigmaConversionError(
                rule,
                rule.source,
                f"Kusto source {identity!r}: conversion results or state unavailable; "
                "convert the complete Sigma collection and preserve callback conversion metadata.",
            ) from error
        if not queries or not states:
            fail(
                "correlations require a convertible source-rule filter; preserve at least one source search and state."
            )
        if len(queries) != len(states):
            fail("source query and conversion state counts do not match; preserve one state per returned search.")
        if any(not isinstance(query, str) or not query.strip() for query in queries):
            fail("source results must be nonempty KQL search strings; check conversion callbacks.")
        if any(
            not isinstance(state, ConversionState) or not isinstance(state.processing_state, dict) for state in states
        ):
            fail("invalid conversion state; preserve each search's ConversionState and processing_state dictionary.")
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
                fail(
                    "chaining requires consistent child result schema and timestamp metadata for every search; "
                    "preserve kusto_result_fields and kusto_result_timestamp from child conversion."
                )
        if child:
            for state in states:
                KustoBackend._child_context_artifact(state.processing_state, source_rule)
        if not child and not all(
            isinstance(table := state.processing_state.get("query_table"), str) and table.strip() for state in states
        ):
            fail(
                "correlations require a source rule with a mapped query table as a nonempty string; "
                "configure the pipeline query_table state."
            )
        return queries, states

    def _count_search(self, rule, allocate):
        definitions, searches = [], []
        context_evidence = self._correlation_columns.get("context_evidence")
        for reference_index, reference in enumerate(rule.referenced_rules):
            source = reference.rule
            for query_index, (query, source_state) in enumerate(
                zip(source.get_conversion_result(), source.get_conversion_states())
            ):
                preserves_context_presence = False
                if isinstance(source, SigmaCorrelationRule):
                    artifact = self._child_context_artifact(source_state.processing_state, source)
                normalization = self.convert_correlation_search_field_normalization_expression(
                    rule.aliases, reference, source_state
                )
                if isinstance(source, SigmaCorrelationRule):
                    state = source_state.processing_state
                    original_query = query
                    query = self._child_raw_query(query, state, source)
                    artifact = state.get("kusto_context_artifact")
                    if artifact:
                        if original_query != state.get("kusto_public_query"):
                            # A callback may narrow summaries. Pair eligible public
                            # keys with complete evidence instead of its display arrays.
                            key_fields = ["WindowEnd", *(source.group_by or [])]
                            keys = ", ".join(self._quote_correlation_field(field) for field in key_fields)
                            eligible = allocate("EligibleChildMatches")
                            complete = allocate("CompleteChildContext")
                            private = ", ".join([keys, artifact["id"], artifact["kind"], *artifact["fields"].values()])
                            scalar_fields = ", ".join(
                                self._quote_correlation_field(field)
                                for field in sorted(state["kusto_result_fields"] - set(state["kusto_context_fields"]))
                            )
                            artifact_query = (
                                f"let {eligible} = () {{\n{query}\n}};\n"
                                f'let {complete} = () {{\n{artifact["query"]}\n}};\n'
                                f"{eligible}() | project {scalar_fields}\n"
                                f"| join kind=inner ({complete}() | project {private}) on {keys}"
                            )
                        else:
                            artifact_query = artifact["query"]
                        function, child_results = self._shared_child_source(
                            source, reference_index, query_index, artifact_query
                        )
                        kind = self._quote_correlation_field(artifact["kind"])
                        selected = "ContextEvidence" if normalization.expands_context else "Summary"
                        relation = f'{child_results}\n| where {kind} == "{selected}"'
                        if normalization.preserve_empty_context or context_evidence and normalization.expands_context:
                            identity = self._quote_correlation_field(artifact["id"])
                            values = ", ".join(
                                self._quote_correlation_field(value) for value in artifact["fields"].values()
                            )
                            # Empty evidence does not erase a positive child match.
                            # A marker keeps its structural row out of retained tuples.
                            # A true child can have no positive evidence (absence).
                            # Keep its summary for temporal membership; mark only
                            # real relationships so string-null defaults never
                            # become fabricated retained tuples.
                            marker = f", {context_evidence} = true" if context_evidence else ""
                            preserves_context_presence = bool(context_evidence)
                            relation = (
                                f'{child_results} | where {kind} == "Summary"\n| project-away {values}\n'
                                f'| join kind=leftouter ({child_results} | where {kind} == "ContextEvidence" '
                                f"| project {identity}, {values}{marker}) on {identity}"
                            )
                        child_id = self._correlation_columns.get("child_match")
                        if child_id:
                            identity = self._quote_correlation_field(artifact["id"])
                            relation += (
                                f'\n| extend {child_id} = strcat({json.dumps(function)}, "_", tostring({identity}))'
                            )
                        relation += normalization
                    else:
                        shared = self._correlation_shared_children["enabled"]
                        if shared:
                            function, child_results = self._shared_child_source(
                                source, reference_index, query_index, query
                            )
                        else:
                            function = allocate(self._child_binding_label(source))
                            definitions.append(f"let {function} = () {{\n{query}\n}};\n")
                            child_results = f"{function}()"
                        relation = f"{child_results}{normalization}"
                    if self._correlation_columns.get("narrow_source"):
                        searches.append(f"({relation})")
                    else:
                        searches.append(
                            f"({relation}\n| extend {normalization.rule_column} = "
                            f"{json.dumps(str(source.name or source.id), ensure_ascii=False)})"
                        )
                else:
                    if self._correlation_columns.get("narrow_source"):
                        searches.append(
                            _CorrelationQueryExpression("({query}{normalization})").format(
                                rule=source, ruleid=None, query=query, normalization=normalization
                            )
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
                if context_evidence and not preserves_context_presence:
                    searches[-1] = f"({searches[-1]}\n| extend {context_evidence} = true)"
        return "".join(definitions), self._combine_correlation_sources(searches)

    def _combine_correlation_sources(self, searches):
        """Avoid a union operator when conversion produced only one source relation."""
        if len(searches) == 1:
            search = searches[0]
            return search[1:-1] if search.startswith("(") and search.endswith(")") else search
        return "union " + self.correlation_search_multi_rule_query_expression_joiner.join(searches)

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

        allocate = self._name_allocator(source_fields)

        event_count = rule.type == SigmaCorrelationType.EVENT_COUNT
        value_count = rule.type == SigmaCorrelationType.VALUE_COUNT
        presence_temporal = rule.type == SigmaCorrelationType.TEMPORAL
        value_sum = rule.type == SigmaCorrelationType.VALUE_SUM
        value_avg = rule.type == SigmaCorrelationType.VALUE_AVG
        value_percentile = rule.type == SigmaCorrelationType.VALUE_PERCENTILE
        columns = {
            "timestamp": allocate("EventTime"),
            "rule": allocate("SourceRule"),
            "groups": [
                allocate("Group" + re.sub(r"[^A-Za-z0-9_]", "_" if ordered else "", field))
                for field in (rule.group_by or [])
            ],
        }
        if event_count or value_count or presence_temporal and not self._uses_context_groups(rule):
            columns["narrow_source"] = True
        if value_sum or value_avg or value_percentile:
            columns["narrow_source"] = True
        if presence_temporal:
            columns["include_source_rule"] = True
        numeric_context = rule.type in (
            SigmaCorrelationType.VALUE_SUM,
            SigmaCorrelationType.VALUE_AVG,
            SigmaCorrelationType.VALUE_PERCENTILE,
        ) and self._uses_context_groups(rule, [*(rule.group_by or []), *(counted_fields or [])])
        if counted_fields is None and not temporal or numeric_context:
            columns["child_match"] = allocate("ChildCorrelationMatch")
        if counted_fields is not None:
            columns["values"] = [
                allocate(("Counted" if value_count else "Measured") + re.sub(r"[^A-Za-z0-9_]", "", field))
                for field in counted_fields
            ]
        self._correlation_group_by = rule.group_by or []
        self._correlation_counted_fields = counted_fields or []
        self._correlation_columns = columns
        try:
            definitions, search = self._count_search(rule, allocate)
            if rule.type in (
                SigmaCorrelationType.VALUE_SUM,
                SigmaCorrelationType.VALUE_AVG,
                SigmaCorrelationType.VALUE_PERCENTILE,
            ):
                for field in counted_fields:
                    if self._correlation_field_types[field] not in ("int", "long", "real", "decimal"):
                        raise SigmaConversionError(
                            rule,
                            rule.source,
                            f"Kusto measured field {field!r} requires a numeric scalar type; explicitly normalize "
                            "numeric strings or other scalar values in the pipeline and update kusto_schema.",
                        )
            if "child_match" in columns and (numeric_context or self._uses_context_groups(rule)):
                source_matches = allocate("ParentSourceMatches")
                child_id = columns["child_match"]
                definitions += _CorrelationRelationBinding(
                    source_matches,
                    f'{search}\n| extend {child_id} = tostring(column_ifexists({json.dumps(child_id)}, ""))',
                    consumer_count=2,
                    completed_reductions=(
                        "project canonical correlation inputs in each source branch",
                        "normalize the optional child match identity before the direct/child split",
                    ),
                ).render()
                child_matches = f"{source_matches} | where isnotempty({child_id})"
                keys = [child_id, *columns["groups"]]
                if numeric_context:
                    # Restriction to observed group/value relationships has already
                    # happened during expansion. Deduplicate only within each child
                    # and resulting group; direct observations retain multiplicity.
                    value = columns["values"][0]
                    scalar_value = allocate("RetainedNumericValue")
                    child_matches += f" | extend {scalar_value} = {value}"
                    keys.append(scalar_value)
                search = (
                    f"union ({source_matches} | where isempty({child_id})), "
                    f"({child_matches} | summarize take_any(*) by {', '.join(keys)})"
                )
        finally:
            del self._correlation_group_by
            del self._correlation_counted_fields
            del self._correlation_columns
        timestamp = columns["timestamp"]
        groups = columns["groups"]
        if len(metrics) > 1:
            metrics = [self._quote_correlation_field(metric) for metric in metrics]
        # Sigma's weeks, months, and years have fixed second lengths. Kusto
        # timespan literals do not accept those units.
        span = rule.timespan.spec if rule.timespan.unit in "smhd" else f"{rule.timespan.seconds}s"
        prepared = _PreparedCorrelationInput(
            rule=rule,
            definitions=definitions,
            search=search,
            columns=columns,
            span=span,
            query_operator=query_operator,
            threshold=threshold,
            allocate=allocate,
        )
        if rule.type == SigmaCorrelationType.EVENT_COUNT:
            existence_threshold = self._count_existence_threshold(rule)
            if existence_threshold is not None:
                return [self._convert_count_existence(prepared, existence_threshold)]
            return [self._convert_event_count(prepared)]
        if value_count:
            return [self._convert_value_count(prepared, counted_fields, metrics)]
        if ordered:
            return [
                _CorrelationQuery(definitions + query.definitions, query.body)
                for query in self._convert_ordered_temporal(prepared, source_type_count)
            ]
        if presence_temporal:
            return [self._convert_temporal_presence(prepared)]
        if value_sum:
            return [self._convert_value_sum(prepared, counted_fields[0])]
        if value_avg:
            return [self._convert_value_avg(prepared)]
        if value_percentile:
            return [self._convert_value_percentile(prepared, counted_fields[0])]

    @staticmethod
    def _scalar_neighbor_changed(field, kind, neighbor="prev"):
        """Compare adjacent scalars with the same NaN identity as native grouping."""
        previous = f"{neighbor}({field})"
        expression = f"{field} != {previous}"
        if kind == "real":
            # Native summarize treats NaNs as one distinct scalar value.
            expression = f"({expression} and not(isnan({field}) and isnan({previous})))"
        return expression

    def _convert_value_avg(self, prepared: _PreparedCorrelationInput):
        """Average the actual current observations; expired values leave no residue."""
        rule, columns, allocate = prepared.rule, prepared.columns, prepared.allocate
        definitions = prepared.definitions
        observations = allocate("AverageObservations")
        event_time, groups, value = columns["timestamp"], columns["groups"], columns["values"][0]
        window_end, time_bin = allocate("AverageWindowEnd"), allocate("AverageTimeBin")
        anchors = ", ".join(
            [*groups, f"{window_end} = {event_time}", f"{time_bin} = bin({event_time}, {prepared.span})"]
        )
        join_keys = ", ".join([*groups, time_bin])
        summary_keys = ", ".join([window_end, *groups])
        result = self._correlation_result_projection(rule, groups, window_end, prepared.span, ["ValueAvg"])
        # Preserve every observation, including duplicates and unusable values.
        # The anchor's own row guarantees exact-time membership even if every
        # other candidate in its bin has already expired.
        definitions += f"let {observations} = {prepared.search};\n"
        return _CorrelationQuery(
            definitions,
            f"{observations}\n| summarize by {anchors}\n"
            f"| join kind=inner (\n{observations}\n"
            f"| extend {time_bin} = pack_array(bin({event_time}, {prepared.span}), bin({event_time}, {prepared.span}) + {prepared.span})\n"
            f"| mv-expand {time_bin} to typeof(datetime)\n) on {join_keys}\n"
            f"| where {event_time} between (({window_end} - {prepared.span}) .. {window_end})\n"
            f"| summarize ValueAvg = avgif(toreal({value}), isfinite(toreal({value}))) by {summary_keys}\n"
            # Empty avgif returns NaN, which otherwise satisfies inequality.
            f"| where isfinite(ValueAvg) and ValueAvg {prepared.query_operator} {prepared.threshold}\n| project {result}",
        )

    def _convert_value_percentile(self, prepared: _PreparedCorrelationInput, measured_field):
        """Select an exact native observation at the inclusive nearest-rank position."""
        rule, columns, allocate = prepared.rule, prepared.columns, prepared.allocate
        definitions = prepared.definitions
        observations = allocate("PercentileObservations")
        candidates = allocate("PercentileWindowValues")
        event_time, groups, value = columns["timestamp"], columns["groups"], columns["values"][0]
        window_end, time_bin = allocate("PercentileWindowEnd"), allocate("PercentileTimeBin")
        kind = self._correlation_field_types[measured_field]
        usable = f"isnotnull({value})"
        if kind == "real":
            usable += f" and isfinite({value})"
        keys = [*groups, window_end]
        key_fields = ", ".join(keys)
        joins = ", ".join([*groups, time_bin])
        anchors = ", ".join(
            [*groups, f"{window_end} = {event_time}", f"{time_bin} = bin({event_time}, {prepared.span})"]
        )
        definitions += f"let {observations} = {prepared.search};\n"
        # Original observations provide anchors even when their measurement is
        # missing. Only usable observations participate in selection.
        pairs = (
            f"{observations}\n| project {anchors}\n| distinct *\n"
            f"| join kind=inner (\n{observations}\n| where {usable}\n"
            f"| extend {time_bin} = pack_array(bin({event_time}, {prepared.span}), bin({event_time}, {prepared.span}) + {prepared.span})\n"
            f"| mv-expand {time_bin} to typeof(datetime)\n) on {joins}\n"
            f"| where {event_time} between (({window_end} - {prepared.span}) .. {window_end})\n"
            f"| project {key_fields}, {value}"
        )
        selector = rule.condition.percentile
        if selector in (0, 100):
            # At the endpoints, native extrema select the same observation
            # without population counting, serialization, or sorting.
            body = (
                pairs
                + f"\n| summarize ValuePercentile = {'min' if selector == 0 else 'max'}({value}) by {key_fields}\n"
            )
        else:
            population, rank = allocate("PercentilePopulation"), allocate("PercentileRank")
            # Reuse the exact candidate relation for counting and selection.
            # Projecting it narrowly keeps the one materialization and sort small.
            definitions += _CorrelationRelationBinding(
                candidates,
                f"\n{pairs}\n",
                consumer_count=2,
                completed_reductions=(
                    "filter unusable measurements",
                    "restrict candidates to exact windows",
                    "project percentile key and measured value",
                ),
            ).render()
            changes = []
            for logical, field in zip(rule.group_by or [], groups):
                changed = self._scalar_neighbor_changed(field, self._correlation_field_types[logical])
                changes.append(changed)
            changes.append(f"{window_end} != prev({window_end})")
            reset = " or ".join(changes)
            order = ", ".join(f"{field} asc" for field in [*keys, value])
            body = (
                f"{candidates}\n| join kind=inner (\n"
                f"{candidates} | summarize {population} = count() by {key_fields}\n) on {key_fields}\n"
                f"| project {key_fields}, {value}, {population}\n"
                f"| sort by {order}\n| serialize {rank} = row_number(1, {reset})\n"
                # Quotient/remainder avoids multiplying an arbitrarily large
                # population by the selector before dividing by 100.
                f"| where {rank} == max_of(1, ({population} / 100) * {selector} + "
                f"tolong(ceiling(toreal(({population} % 100) * {selector}) / 100.0)))\n"
                f"| extend ValuePercentile = {value}\n"
            )
        comparison = f"ValuePercentile {prepared.query_operator} {prepared.threshold}"
        if kind == "decimal":
            comparison = f"ValuePercentile {prepared.query_operator} decimal({prepared.threshold})"
        elif kind in ("int", "long") and prepared.threshold != int(prepared.threshold):
            comparison = f"todecimal(ValuePercentile) {prepared.query_operator} decimal({prepared.threshold})"
        projection = self._correlation_result_projection(rule, groups, window_end, prepared.span, ["ValuePercentile"])
        return _CorrelationQuery(definitions, body + f"| where {comparison}\n| project {projection}")

    def _convert_value_sum(self, prepared: _PreparedCorrelationInput, measured_field):
        """Use exact integer boundaries and current-window native noninteger sums."""
        rule, columns, allocate = prepared.rule, prepared.columns, prepared.allocate
        definitions = prepared.definitions
        sources = allocate("SumObservations")
        event_time, groups, value = columns["timestamp"], columns["groups"], columns["values"][0]
        kind = self._correlation_field_types[measured_field]
        comparison = f"ValueSum {prepared.query_operator} {prepared.threshold}"
        if kind == "decimal":
            comparison = f"ValueSum {prepared.query_operator} decimal({prepared.threshold})"
        elif kind in ("int", "long") and prepared.threshold != int(prepared.threshold):
            comparison = f"todecimal(ValueSum) {prepared.query_operator} decimal({prepared.threshold})"
        definitions += f"let {sources} = {prepared.search};\n"
        if kind in ("int", "long"):
            arrivals = allocate("SumArrivals")
            arrival_sum, boundary_time, boundary_order, delta = [
                allocate(name) for name in ("ArrivalSum", "SumBoundaryTime", "SumBoundaryOrder", "SumDelta")
            ]
            keys = ", ".join([event_time, *groups])
            definitions += f"let {arrivals} = {sources}\n| summarize {arrival_sum} = sum({value}) by {keys};\n"
            retained = ", ".join(groups)
            retained = retained + ", " if retained else ""
            order = ", ".join(f"{field} asc" for field in [*groups, boundary_time, boundary_order])
            changes = []
            for logical, field in zip(rule.group_by or [], groups):
                changed = self._scalar_neighbor_changed(field, self._correlation_field_types[logical])
                changes.append(changed)
            restart = ", " + " or ".join(changes) if changes else ""
            body = (
                f"union ({arrivals}\n| project {retained}{boundary_time} = {event_time}, "
                f"{boundary_order} = 0, {delta} = {arrival_sum}),\n"
                f"({arrivals}\n| project {retained}{boundary_time} = {event_time} + {prepared.span}, "
                f"{boundary_order} = 1, {delta} = -{arrival_sum})\n"
                "// Arrivals evaluate before equal-time expirations, including zero and missing values.\n"
                f"| sort by {order}\n| extend ValueSum = row_cumsum({delta}{restart})\n"
                f"| where {boundary_order} == 0\n"
            )
            end = boundary_time
        else:
            end, time_bin = allocate("SumWindowEnd"), allocate("SumTimeBin")
            anchors = ", ".join([*groups, f"{end} = {event_time}", f"{time_bin} = bin({event_time}, {prepared.span})"])
            join_keys = ", ".join([*groups, time_bin])
            summary = ", ".join([end, *groups])
            aggregate = f"sumif({value}, isfinite({value}))" if kind == "real" else f"sum({value})"
            # Every source row remains on the right so its own anchor survives,
            # even when no finite observation belongs to its exact window.
            body = (
                f"{sources}\n| project {anchors}\n| distinct *\n"
                f"| join kind=inner (\n{sources}\n"
                f"| extend {time_bin} = pack_array(bin({event_time}, {prepared.span}), bin({event_time}, {prepared.span}) + {prepared.span})\n"
                f"| mv-expand {time_bin} to typeof(datetime)\n) on {join_keys}\n"
                f"| where {event_time} between (({end} - {prepared.span}) .. {end})\n"
                f"| summarize ValueSum = {aggregate} by {summary}\n"
            )
        projection = self._correlation_result_projection(rule, groups, end, prepared.span, ["ValueSum"])
        return _CorrelationQuery(definitions, body + f"| where {comparison}\n| project {projection}")

    def _convert_value_count(self, prepared: _PreparedCorrelationInput, counted_fields, metrics):
        """Merge each scalar's active intervals, then count at original source anchors."""
        rule, columns, allocate = prepared.rule, prepared.columns, prepared.allocate
        definitions = prepared.definitions
        matches = allocate("ValueCountSources")
        event_time, groups = columns["timestamp"], columns["groups"]
        boundary_time, boundary_order = [allocate(name) for name in ("ValueBoundaryTime", "ValueBoundaryOrder")]
        deltas = [allocate(value + "Delta") for value in columns["values"]]
        definitions += _CorrelationRelationBinding(
            matches,
            prepared.search,
            consumer_count=1 + len(counted_fields),
            completed_reductions=("project and validate canonical value-count source columns",),
        ).render()

        branches = []

        def boundary(source, time, order, active=None, amount=0):
            projection = [*groups, f"{boundary_time} = {time}", f"{boundary_order} = {order}"]
            projection.extend(f"{delta} = {amount if index == active else 0}" for index, delta in enumerate(deltas))
            return f"({source}\n| project {', '.join(projection)})"

        anchor_fields = ", ".join([*groups, event_time])
        branches.append(boundary(f"{matches} | summarize by {anchor_fields}", event_time, 1))
        group_kinds = [self._correlation_field_types[field] for field in rule.group_by or []]
        for index, (field, value) in enumerate(zip(counted_fields, columns["values"])):
            intervals = allocate(value + "Activity")
            starts, ends = [allocate(value + suffix) for suffix in ("Starts", "Ends")]
            keys = [*groups, value]
            kinds = [*group_kinds, self._correlation_field_types[field]]
            start = " or ".join(
                [
                    f"isnull(prev({event_time}))",
                    *(self._scalar_neighbor_changed(key, kind) for key, kind in zip(keys, kinds)),
                    f"{event_time} > prev({event_time}) + {prepared.span}",
                ]
            )
            end = " or ".join(
                [
                    f"isnull(next({event_time}))",
                    *(self._scalar_neighbor_changed(key, kind, "next") for key, kind in zip(keys, kinds)),
                    f"next({event_time}) > {event_time} + {prepared.span}",
                ]
            )
            definitions += _CorrelationRelationBinding(
                intervals,
                f"{matches}\n| where isnotempty({value})\n"
                f"| summarize by {', '.join([*keys, event_time])}\n"
                f"| sort by {', '.join(f'{key} asc' for key in [*keys, event_time])}\n"
                "// Repeated values whose inclusive active intervals overlap contribute only once.\n"
                f"| extend {starts} = {start},\n    {ends} = {end}",
                consumer_count=2,
                completed_reductions=(
                    "exclude unusable counted values without removing anchors",
                    "deduplicate group/value/timestamp activity identities",
                    "mark merged inclusive interval boundaries",
                ),
            ).render()
            branches.append(boundary(f"{intervals} | where {starts}", event_time, 0, index, 1))
            branches.append(boundary(f"{intervals} | where {ends}", f"{event_time} + {prepared.span}", 2, index, -1))
        restart = " or ".join(self._scalar_neighbor_changed(group, kind) for group, kind in zip(groups, group_kinds))
        accumulators = [
            f"{metric} = row_cumsum({delta}" + (f", {restart})" if restart else ")")
            for metric, delta in zip(metrics, deltas)
        ]
        comparisons = " and ".join(
            (
                f"todecimal({metric}) {prepared.query_operator} decimal({prepared.threshold})"
                if prepared.threshold != int(prepared.threshold)
                else f"{metric} {prepared.query_operator} {prepared.threshold}"
            )
            for metric in metrics
        )
        projection = self._correlation_result_projection(rule, groups, boundary_time, prepared.span, metrics)
        body = (
            "union " + ",\n".join(branches) + "\n"
            "// All interval starts precede anchors; equal-time expirations follow them.\n"
            f"| sort by {', '.join(f'{key} asc' for key in [*groups, boundary_time, boundary_order])}\n"
            f"| extend {', '.join(accumulators)}\n"
            f"| where {boundary_order} == 1\n| where {comparisons}\n| project {projection}"
        )
        return _CorrelationQuery(definitions, body)

    def _convert_count_existence(self, prepared: _PreparedCorrelationInput, threshold):
        """Keep a timestamp when its Nth-most-recent source match fits."""
        rule, columns, allocate = prepared.rule, prepared.columns, prepared.allocate
        definitions = prepared.definitions
        event_time, groups = columns["timestamp"], columns["groups"]
        body = f"{prepared.search}\n"
        if threshold > 1:
            offset = threshold - 1
            predecessor = allocate("ThresholdPredecessorTime")
            same_group = []
            for logical, group in zip(rule.group_by or [], groups):
                equality = f"{group} == prev({group}, {offset})"
                if self._correlation_field_types[logical] == "real":
                    equality = f"({equality} or (isnan({group}) and isnan(prev({group}, {offset}))))"
                same_group.append(equality)
            body += (
                "// Preserve individual source matches, including timestamp ties, before the threshold lookup.\n"
                f"| sort by {', '.join(f'{field} asc' for field in [*groups, event_time])}\n"
                f"| extend {predecessor} = prev({event_time}, {offset})\n"
                f"| where {predecessor} >= {event_time} - {prepared.span}"
                + (f" and {' and '.join(same_group)}" if same_group else "")
                + "\n"
            )
        body += (
            f"| distinct {', '.join([event_time, *groups])}\n"
            f"| project {self._correlation_result_projection(rule, groups, event_time, prepared.span, [])}"
        )
        return _CorrelationQuery(definitions, body)

    def _convert_temporal_presence(self, prepared: _PreparedCorrelationInput):
        """Evaluate inclusive source presence once at each source-match timestamp."""
        rule, columns, allocate = prepared.rule, prepared.columns, prepared.allocate
        definitions = prepared.definitions
        event_time, source_rule = columns["timestamp"], columns["rule"]
        groups = columns["groups"]
        sources = list({id(ref.rule): ref.rule for ref in rule.referenced_rules}.values())
        membership, last_seen, presence = {}, {}, {}
        for source in sources:
            name = re.sub(r"[^A-Za-z_0-9]", "", source.name or source.title or str(source.id)) or "Source"
            name = name[0].upper() + name[1:]
            membership[id(source)] = allocate("Matches" + name)
            last_seen[id(source)] = allocate("Last" + name + "Time")
            presence[id(source)] = allocate("Has" + name)
        group_start = allocate("NewCorrelationGroup")
        scan_step = allocate("PresenceWindow")
        changes = [f"isnull(prev({event_time}))"]
        for logical, field in zip(rule.group_by or [], groups):
            changed = self._scalar_neighbor_changed(field, self._correlation_field_types[logical])
            changes.append(changed)
        memberships = ", ".join(
            f"{membership[id(source)]} = countif({source_rule} == "
            f"{json.dumps(str(source.name or source.id), ensure_ascii=False)}) > 0"
            for source in sources
        )
        declarations = ", ".join(f"{last_seen[id(source)]}:datetime" for source in sources)
        updates = ",\n        ".join(
            f"{last_seen[id(source)]} = iff({membership[id(source)]}, {event_time}, "
            f"iff({group_start}, datetime(null), {scan_step}.{last_seen[id(source)]}))"
            for source in sources
        )
        present = ", ".join(
            f"{presence[id(source)]} = isnotnull({last_seen[id(source)]}) and "
            f"{last_seen[id(source)]} >= {event_time} - {prepared.span}"
            for source in sources
        )
        rule_count = " + ".join(f"tolong({presence[id(source)]})" for source in sources)
        if isinstance(rule.condition, SigmaExtendedCorrelationCondition):
            references = {ref.reference: presence[id(ref.rule)] for ref in rule.referenced_rules}
            comparison = self._boolean_temporal_expression(rule, rule.condition.parsed, references)
        elif prepared.threshold != int(prepared.threshold):
            comparison = f"todecimal(RuleCount) {prepared.query_operator} decimal({prepared.threshold})"
        else:
            comparison = f"RuleCount {prepared.query_operator} {prepared.threshold}"
        keys = ", ".join([*groups, event_time])
        order = ", ".join(f"{field} asc" for field in [*groups, event_time])
        projection = self._correlation_result_projection(rule, groups, event_time, prepared.span, ["RuleCount"])
        body = (
            f"{prepared.search}\n"
            "// Batch simultaneous source matches before updating inclusive trailing presence.\n"
            f"| summarize {memberships} by {keys}\n"
            f"| sort by {order}\n"
            f"| extend {group_start} = {' or '.join(changes)}\n"
            f"| scan declare ({declarations}) with (\n"
            f"    step {scan_step}: true =>\n        {updates};\n)\n"
            "// Only source-match timestamps evaluate; expiration never creates an anchor.\n"
            f"| extend {present}\n"
            f"| extend RuleCount = {rule_count}\n"
            f"| where {comparison}\n| project {projection}"
        )
        return _CorrelationQuery(definitions, body)

    def _convert_event_count(self, prepared: _PreparedCorrelationInput):
        """Accumulate integer source-match weights at arrivals and expirations."""
        rule, columns, allocate = prepared.rule, prepared.columns, prepared.allocate
        definitions = prepared.definitions
        arrivals = allocate("SourceMatchArrivals")
        event_time = columns["timestamp"]
        match_count = allocate("SourceMatchCount")
        boundary_time = allocate("CountBoundaryTime")
        boundary_order = allocate("CountBoundaryOrder")
        delta = allocate("SourceMatchDelta")
        groups = columns["groups"]
        retained = ", ".join(groups)
        retained = f"{retained}, " if retained else ""
        order = ", ".join(f"{field} asc" for field in [*groups, boundary_time, boundary_order])
        changes = []
        for logical, field in zip(rule.group_by or [], groups):
            changed = self._scalar_neighbor_changed(field, self._correlation_field_types[logical])
            changes.append(changed)
        restart = " or ".join(changes)
        cumulative = f"row_cumsum({delta}" + (f", {restart})" if restart else ")")
        comparison = f"EventCount {prepared.query_operator} {prepared.threshold}"
        if prepared.threshold != int(prepared.threshold):
            # Kusto equality can coerce a real literal to the long column type.
            # Decimal comparison preserves fractional thresholds and exact longs.
            comparison = f"todecimal(EventCount) {prepared.query_operator} decimal({prepared.threshold})"
        projection = self._correlation_result_projection(rule, groups, boundary_time, prepared.span, ["EventCount"])
        definitions += (
            f"let {arrivals} = {prepared.search}\n"
            f"| summarize {match_count} = count() by {event_time}"
            + (f", {', '.join(groups)}" if groups else "")
            + ";\n"
        )
        # One arrival per group/timestamp is both its complete positive delta
        # and its only evaluation anchor. Expirations at T follow that anchor,
        # preserving the inclusive [T - span, T] endpoint without adding ticks.
        body = (
            f"union ({arrivals}\n| project {retained}{boundary_time} = {event_time}, "
            f"{boundary_order} = 0, {delta} = {match_count}),\n"
            f"({arrivals}\n| project {retained}{boundary_time} = {event_time} + {prepared.span}, "
            f"{boundary_order} = 1, {delta} = -{match_count})\n"
            "// Arrivals evaluate before equal-time expirations; expirations never create matches.\n"
            f"| sort by {order}\n"
            f"| extend EventCount = {cumulative}\n"
            f"| where {boundary_order} == 0\n"
            f"| where {comparison}\n"
            f"| project {projection}"
        )
        return _CorrelationQuery(definitions, body)

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

    def _convert_ordered_temporal(self, prepared: _PreparedCorrelationInput, source_type_count):
        rule, columns, allocate = prepared.rule, prepared.columns, prepared.allocate
        groups = columns["groups"]
        identities = [reference.rule.name or str(reference.rule.id) for reference in rule.referenced_rules]
        labels = [
            "".join(part[:1].upper() + part[1:] for part in re.findall(r"[A-Za-z0-9]+", identity)) or "Source"
            for identity in identities
        ]
        timestamp = columns["timestamp"]
        complete = allocate("CompleteNow")
        new_group = allocate("NewCorrelationGroup")
        state = allocate("SequenceState")
        memberships = {}
        for identity, label in zip(identities, labels):
            if identity not in memberships:
                memberships[identity] = allocate(f"Has{label}")
        aggregates = ", ".join(
            f"{name} = countif({columns['rule']} == {json.dumps(identity, ensure_ascii=False)}) > 0"
            for identity, name in memberships.items()
        )
        batch_fields = ", ".join([timestamp, *groups])
        batch_summary = f"{aggregates} by {batch_fields}" if len(identities) > 1 else f"by {batch_fields}"
        sequence = " -> ".join(json.dumps(identity, ensure_ascii=False) for identity in identities)
        query = (
            f"// Strict sequence: {sequence}. Total inclusive span: {prepared.span}.\n"
            f"{prepared.search}\n"
            "// Batch equal timestamps: simultaneous source matches cannot advance strict sequence positions.\n"
            f"| summarize {batch_summary}\n"
        )
        if len(identities) > 1:
            prefixes = [allocate(f"Latest{'Then'.join(labels[:index])}Start") for index in range(1, len(identities))]
            first_source = rule.referenced_rules[0].rule
            if len(identities) == 2 and self._count_existence_threshold(first_source) is not None:
                prefixes[0] = allocate(f"Last{labels[0].removesuffix('s')}Time")
            group_changes = []
            for field, group in zip(rule.group_by or [], groups):
                changed = self._scalar_neighbor_changed(group, self._correlation_field_types[field])
                group_changes.append(changed)
            reset = " or ".join([f"isnull(prev({timestamp}))", *group_changes])
            query += (
                f"| sort by {', '.join(f'{field} asc' for field in [*groups, timestamp])}\n"
                f"| extend {new_group} = {reset}\n"
                "// Read preceding timestamp state for every assignment; preserve viable earlier prefixes.\n"
                f"| scan declare ({', '.join(f'{prefix}:datetime = datetime(null)' for prefix in prefixes)}, "
                f"{complete}:bool = false) with (\n"
                f"    step {state}: true =>\n"
                f"        {complete} = not({new_group}) and {memberships[identities[-1]]} "
                f"and isnotnull({state}.{prefixes[-1]}) and {state}.{prefixes[-1]} >= {timestamp} - {prepared.span},\n"
            )
            assignments = []
            for index, prefix in enumerate(prefixes):
                # For future detection, the latest start dominates earlier
                # starts with the same processed timestamp boundary. A new
                # first-step event never overwrites a longer viable prefix.
                previous = f"{state}.{prefix}"
                if index == 0:
                    value = (
                        f"iff({memberships[identities[0]]}, {timestamp}, iff({new_group}, datetime(null), {previous}))"
                    )
                else:
                    predecessor = f"{state}.{prefixes[index - 1]}"
                    advance = (
                        f"{memberships[identities[index]]} and isnotnull({predecessor}) "
                        f"and {predecessor} >= {timestamp} - {prepared.span} "
                        f"and (isnull({previous}) or {predecessor} > {previous})"
                    )
                    value = f"iff({new_group}, datetime(null), iff({advance}, {predecessor}, {previous}))"
                assignments.append(f"        {prefix} = {value}")
            query += ",\n".join(assignments) + ";\n)\n" + f"| where {complete}\n"
        result_fields = self._correlation_result_projection(
            rule, groups, timestamp, prepared.span, [f"RuleCount = tolong({source_type_count})"]
        )
        return [_CorrelationQuery("", query + f"| project {result_fields}")]

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

    @staticmethod
    def _correlation_scalar_type(source, state, field):
        """Resolve the final source column; custom schemas describe post-transform types."""
        metadata = state.processing_state
        if isinstance(source, SigmaCorrelationRule):
            kind = metadata.get("kusto_result_types", {}).get(field)
        else:
            schema = metadata.get("kusto_schema")
            kind = schema.get_field_type(metadata.get("query_table"), field) if schema is not None else None
            if kind is None and schema is not None:
                # A pure field rename preserves the declared input type. Final-column
                # declarations take precedence when the pipeline also transforms values.
                candidates = {field}
                mappings = [
                    getattr(item.transformation, "mapping", {})
                    for item in metadata.get("kusto_field_transformations", ())
                ]
                builtin = metadata.get("kusto_field_mappings")
                if builtin is not None:
                    mappings = [
                        builtin.table_mappings.get(metadata.get("query_table"), {}),
                        builtin.generic_mappings,
                        *mappings,
                    ]
                for mapping in reversed(mappings):
                    candidates.update(
                        original
                        for original, targets in mapping.items()
                        if any(target in candidates for target in (targets if isinstance(targets, list) else [targets]))
                    )
                kinds = {schema.get_field_type(metadata.get("query_table"), name) for name in candidates}
                kinds.discard(None)
                if len(kinds) == 1:
                    kind = kinds.pop()
        kind = (
            {"boolean": "bool", "double": "real", "float": "real", "int64": "long", "int32": "int"}.get(
                str(kind).lower(), str(kind).lower()
            )
            if kind
            else None
        )
        location = metadata.get("query_table", "child result")
        if kind is None:
            raise SigmaConversionError(
                source,
                source.source,
                f"Kusto source {source.name or source.id!r}: cannot resolve type of field {field!r} in {location!r}. "
                "Declare this relevant column in pipeline vars['kusto_schema'] using BaseSchema/TableSchema/FieldInfo; "
                "the schema must describe its type after pipeline transformations.",
            )
        if kind not in {"bool", "int", "long", "real", "decimal", "string", "datetime", "timespan", "guid"}:
            raise SigmaConversionError(
                source,
                source.source,
                f"Kusto source {source.name or source.id!r}: field {field!r} in {location!r} has type {kind!r}. "
                "Correlations require native scalar fields; explicitly extract or normalize arrays, property bags, "
                "and mixed dynamic values in the pipeline and declare the resulting scalar type in kusto_schema.",
            )
        return kind

    def convert_correlation_search_field_normalization_expression(self, aliases, rule_reference, source_state=None):
        source_state = source_state or rule_reference.rule.get_conversion_states()[0]
        child = isinstance(rule_reference.rule, SigmaCorrelationRule)

        def map_field(field):
            if child:
                if field == "ContextComplete":
                    raise SigmaConversionError(
                        rule_reference.rule,
                        rule_reference.rule.source,
                        "ContextComplete is display-only and cannot be consumed by a parent correlation; use retained scalar source fields instead.",
                    )
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
            mapped = self._map_correlation_source_field(rule_reference.rule, source_state, field)
            self._correlation_scalar_type(rule_reference.rule, source_state, mapped)
            return mapped

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
        timestamp_type = self._correlation_scalar_type(rule_reference.rule, source_state, timestamp_field)
        columns = self._correlation_columns
        source_timestamp = self._quote_correlation_field(timestamp_field)
        canonical_timestamp = source_timestamp if timestamp_type == "datetime" else f"todatetime({source_timestamp})"
        fields = [f"{columns['timestamp']} = {canonical_timestamp}"]
        source_filter = ""
        if not child:
            names = getattr(self, "_correlation_report_names", {})
            conditions = []
            if "start" in names:
                conditions.append(f"{canonical_timestamp} >= {names['start']} - {self._correlation_report_lookback}s")
            if "end" in names:
                conditions.append(f"{canonical_timestamp} < {names['end']}")
            if conditions:
                source_filter = "\n| where " + " and ".join(conditions)
        context_fields = source_state.processing_state.get("kusto_context_fields", ()) if child else ()
        expanded = False

        def record_type(logical, physical):
            kind = self._correlation_scalar_type(rule_reference.rule, source_state, physical)
            previous = self._correlation_field_types.setdefault(logical, kind)
            if kind != previous:
                source = rule_reference.rule
                raise SigmaConversionError(
                    source,
                    source.source,
                    f"Kusto correlation field {logical!r} has incompatible source types {previous!r} and {kind!r} "
                    f"(source {source.name or source.id!r}, column {physical!r}). "
                    "Explicitly normalize source fields to one scalar type in the pipeline and update kusto_schema.",
                )
            return kind

        def native_scalar(field):
            nonlocal expanded
            if field in context_fields:
                expanded = True
                artifact = source_state.processing_state.get("kusto_context_artifact")
                if not artifact:
                    raise SigmaConversionError(
                        rule_reference.rule,
                        rule_reference.rule.source,
                        "Kusto retained child fields require complete relational context metadata; preserve conversion state.",
                    )
                return self._quote_correlation_field(artifact["fields"][field])
            return self._quote_correlation_field(field)

        for index, field in enumerate(self._correlation_group_by):
            mapped_field = map_field(map_alias(field))
            record_type(field, mapped_field)
            scalar = native_scalar(mapped_field)
            fields.append(f"{columns['groups'][index]} = {scalar}")
        retained_group = expanded
        for index, counted_field in enumerate(self._correlation_counted_fields):
            mapped_field = map_field(map_alias(counted_field))
            record_type(counted_field, mapped_field)
            value = native_scalar(mapped_field)
            fields.append(f"{columns['values'][index]} = {value}")
        for field, column in zip(getattr(self, "_correlation_context_fields", []), columns.get("context", [])):
            mapped_field = map_field(map_alias(field))
            record_type(field, mapped_field)
            scalar = native_scalar(mapped_field)
            fields.append(f"{column} = {scalar}")
        if (
            columns.get("narrow_source")
            and columns.get("child_match")
            and child
            and source_state.processing_state.get("kusto_context_artifact")
        ):
            fields.append(columns["child_match"])
        if columns.get("narrow_source") and columns.get("include_source_rule"):
            identity = json.dumps(str(rule_reference.rule.name or rule_reference.rule.id), ensure_ascii=False)
            fields.append(f"{columns['rule']} = {identity}")
        operator = "project" if columns.get("narrow_source") else "extend"
        valid_rows = " and ".join(
            [f"isnotnull({columns['timestamp']})", *(f"isnotempty({field})" for field in columns["groups"])]
        )
        result = _CorrelationNormalization(
            source_filter + f"\n| {operator} " + ", ".join(fields) + f"\n| where {valid_rows}", columns["rule"]
        )
        result.expands_context = expanded
        result.preserve_empty_context = bool(
            expanded
            and self._correlation_counted_fields
            and not retained_group
            and not getattr(self, "_correlation_context_fields", [])
        )
        return result

    @staticmethod
    def _quote_correlation_field(field):
        return "[" + json.dumps(field, ensure_ascii=False) + "]"
