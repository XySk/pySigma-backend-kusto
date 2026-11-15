# pySigma Kusto Query Language (KQL) Backend

![Tests](https://github.com/AttackIQ/pySigma-backend-microsoft365defender/actions/workflows/test.yml/badge.svg)
![Coverage Badge](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/slincoln-aiq/9c0879725c7f94387801390bbb0ac8d6/raw/slincoln-aiq-pySigma-backend-microsoft365defender.json)
![Status](https://img.shields.io/badge/Status-pre--release-orange)
![PyPI version](https://badge.fury.io/py/pysigma-backend-kusto.svg)
![Python versions](https://img.shields.io/pypi/pyversions/pysigma-backend-kusto.svg)
![pySigma version](https://img.shields.io/badge/pySigma-%5E2.1.0-blue)
![License](https://img.shields.io/github/license/AttackIQ/pySigma-backend-microsoft365defender.svg)

## Contents

- [pySigma Kusto Query Language (KQL) Backend](#pysigma-kusto-query-language-kql-backend)
  - [📖 Overview](#-overview)
    - [🔑 Key Features](#-key-features)
    - [🧑‍💻 Maintainer](#-maintainer)
  - [🚀 Quick Start](#-quick-start)
  - [📘 Usage](#-usage)
    - [🖥️ sigma-cli](#️-sigma-cli)
    - [🐍 Python Script](#-python-script)
  - [🛠️ Advanced Features](#️-advanced-features)
    - [Count and temporal correlations](#count-and-temporal-correlations)
    - [🔄 Pipeline \& Backend Args (New in 0.2.0)](#-pipeline--backend-args-new-in-020)
    - [🗃️ Custom Table Names (New in 0.3.0) (Beta)](#️-custom-table-names-new-in-030-beta)
  - [🔄 Processing Pipelines](#-processing-pipelines)
    - [📊 Rule Support](#-rule-support)
    - [🖥️ Commonly Supported Categories](#️-commonly-supported-categories)
  - [🧪 Custom Transformations](#-custom-transformations)
    - [📊 Custom Postprocessing Item](#-custom-postprocessing-item)
  - [❓Frequently Asked Questions](#frequently-asked-questions)
    - [How do I set the table name for a rule?](#how-do-i-set-the-table-name-for-a-rule)
    - [How do I set the table name for a rule in YAML?](#how-do-i-set-the-table-name-for-a-rule-in-yaml)
    - [How is the table name determined for a rule?](#how-is-the-table-name-determined-for-a-rule)
    - [How are field mappings determined for a rule?](#how-are-field-mappings-determined-for-a-rule)
    - [What tables are supported for each pipeline?](#what-tables-are-supported-for-each-pipeline)
    - [I am receiving an `Invalid SigmaDetectionItem field name encountered` error. What does this mean?](#i-am-receiving-an-invalid-sigmadetectionitem-field-name-encountered-error-what-does-this-mean)
    - [My query\_table or custom field mapping isn't working](#my-query_table-or-custom-field-mapping-isnt-working)
  - [👨‍💻 Developer Guide](#-developer-guide)
    - [Factory Pattern for ProcessingItems (v1.0+)](#factory-pattern-for-processingitems-v10)
  - [🤝 Contributing](#-contributing)
  - [📄 License](#-license)

## 📖 Overview

The **pySigma Kusto Backend** transforms Sigma Rules into queries using [Kusto Query Language (KQL)](https://learn.microsoft.com/en-us/kusto/query/?view=microsoft-fabric). This backend supports multiple Microsoft products, including:

- [Microsoft XDR Advanced Hunting Queries](https://learn.microsoft.com/en-us/defender-xdr/advanced-hunting-overview) (Formally Microsoft 365 Defender Advanced Hunting Queries)
- [Azure Sentinel Advanced Security Information Model (ASIM) Queries](https://learn.microsoft.com/en-us/azure/sentinel/normalization)
- [Azure Monitor Queries](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/get-started-queries)

> **Note:** This backend was previously named **pySigma Microsoft 365 Defender Backend**.

### 🔑 Key Features

- **Backend**: `sigma.backends.kusto` with `KustoBackend` class
- **Pipelines**: Provides `microsoft_xdr_pipeline`, `sentinelasim_pipeline`, and `azure_monitor_pipeline` for query tables and field renames
- **Output**: Query strings in Kusto Query Language (KQL)
- **pySigma v2.1.0+**: Uses pySigma's current correlation parser and dependency ordering

### 🧑‍💻 Maintainer

- [Stephen Lincoln](https://github.com/slincoln-aiq) via [AttackIQ](https://github.com/AttackIQ)

## 🚀 Quick Start

1. Install the package:

   ```bash
   pip install pysigma-backend-kusto
   ```

   > **Note:** This package requires pySigma 2.1 or higher within the 2.x series and Python 3.11+.

2. Convert a Sigma rule to MIcrosoft XDR KQL query using sigma-cli:

   ```bash
   sigma convert -t kusto -p microsoft_xdr path/to/your/rule.yml
   ```

3. Or use in a Python script:

   ```python
   from sigma.rule import SigmaRule

   from sigma.backends.kusto import KustoBackend
   from sigma.pipelines.microsoftxdr import microsoft_xdr_pipeline

   # Load your Sigma rule
   rule = SigmaRule.from_yaml(
      """
      title: Mimikatz CommandLine
      status: test
      logsource:
            category: process_creation
            product: windows
      detection:
            sel:
               CommandLine|contains: mimikatz.exe
            condition: sel
      """
   )

   # Convert the rule
   xdr_pipeline = microsoft_xdr_pipeline()
   backend = KustoBackend(processing_pipeline=xdr_pipeline)
   print(backend.convert_rule(rule)[0])

   ```

## 📘 Usage

### 🖥️ sigma-cli

Use with `sigma-cli` per [typical sigma-cli usage](https://github.com/SigmaHQ/sigma-cli#usage):

```bash
sigma convert -t kusto -p microsoft_xdr -f default -s ~/sigma/rules
```

### 🐍 Python Script

Use the backend and pipeline in a standalone Python script. The pipeline is passed to the backend during initialization.

```python
from sigma.rule import SigmaRule
from sigma.backends.kusto import KustoBackend
from sigma.pipelines.microsoftxdr import microsoft_xdr_pipeline

# Define an example rule as a YAML str
sigma_rule = SigmaRule.from_yaml("""
  title: Mimikatz CommandLine
  status: test
  logsource:
      category: process_creation
      product: windows
  detection:
      sel:
          CommandLine|contains: mimikatz.exe
      condition: sel
""")

# Create pipeline and backend
# Note: In pySigma v1.0+, the pipeline is initialized once and passed to the backend
pipeline = microsoft_xdr_pipeline()
kusto_backend = KustoBackend(processing_pipeline=pipeline)

# Convert the rule
print(sigma_rule.title + " KQL Query: \n")
print(kusto_backend.convert_rule(sigma_rule)[0])
```

Output:

```text
Mimikatz CommandLine KQL Query: 

DeviceProcessEvents
| where ProcessCommandLine contains "mimikatz.exe"
```

## 🛠️ Advanced Features

### Count and temporal correlations

Convert a Sigma collection containing source rules and an `event_count`, `value_count`, `temporal`, or `temporal_ordered` correlation with the configured event timestamp:

```python
from pathlib import Path
from sigma.collection import SigmaCollection
from sigma.backends.kusto import KustoBackend
from sigma.pipelines.microsoftxdr import microsoft_xdr_pipeline

collection = SigmaCollection.from_yaml(Path("rules.yml").read_text())
backend = KustoBackend(
    processing_pipeline=microsoft_xdr_pipeline(),
    correlation_timestamp_field="Timestamp",
)
queries = backend.convert(collection)
```

A correlation combines every converted source search with `union`, including multiple searches emitted for one source rule and searches from different tables. Each branch retains its source columns and adds a mapped `datetime` timestamp, `string` grouping and counted fields, and the source rule's name (or ID when unnamed). Correlation field aliases work for grouping and counted fields in single-source and multi-source correlations. Timestamp, grouping, and counted fields use each source's mappings. All searches emitted for one source rule must use the same mapped table.

Event counts use **exact sliding windows by default**. For each distinct valid matching-event timestamp in each correlation group, the query counts all source matches in the inclusive interval `[timestamp - timespan, timestamp]`, then applies a single `gt`, `gte`, `lt`, `lte`, `eq`, or `neq` comparison. For example, events at 12:00:59 and 12:05:01 belong together in the five-minute window ending at 12:05:01. Events exactly five minutes apart also belong together. Simultaneous matches participate in one evaluation with their complete count. Matching several source rules or searches contributes a row for every match; physical events are not deduplicated.

For `value_count`, specify the counted field in the condition:

```yaml
correlation:
  type: value_count
  rules: [source-rule]
  group-by: [DeviceName]
  timespan: 5m
  condition:
    field: User
    gte: 2
```

Value counts use the same exact sliding windows and six comparisons. Each window counts distinct nonempty string-normalized values across all its source matches. A value repeated at different timestamps or across searches and rules counts once. Null or empty counted values contribute no value, but their otherwise valid events still establish evaluation timestamps. Such an anchored window can have `ValueCount = 0`, and may satisfy `eq: 0`. A scalar field or a one-element list returns the established `ValueCount` metric.

For `value_sum`, specify one measured field and a comparison threshold, including a fractional threshold:

```yaml
correlation:
  type: value_sum
  rules: [source-rule]
  group-by: [DeviceName]
  timespan: 5m
  condition:
    field: Bytes
    gte: 12.5
```

The `ValueSum` metric adds every finite numeric source match in the inclusive sliding window. Repeated matches contribute repeatedly; zero and negative values count. Text, missing values, NaN, and infinities contribute nothing, and numeric strings are not parsed. A valid match without a numeric value still establishes a window end, so an anchored window with no numeric observations has sum zero. Integer-only windows preserve exact sums from −9,223,372,036,854,775,808 through 9,223,372,036,854,775,807. Integer observations accumulate as decimal values before a query-wide range check: an out-of-range anchored integer-only window fails explicitly with `Kusto value_sum integer overflow`, even if the rule comparison would reject that window. Opposite-sign observations can cancel before this range check; native Kusto long aggregation would silently wrap. Windows containing real values use ordinary floating-point addition, with direct comparisons and no tolerance near a threshold. The result metric is dynamic so it can retain either an integer or a real value. The numeric fixtures have passed execution against the Microsoft Kusto emulator; see [runtime evidence](tests/fixtures/correlation/RUNTIME.md). Sum queries materialize window summaries for the range check and share Kusto’s 5 GB per-node materialization cache limit; exhaustion fails the query.

For `value_percentile`, use `condition: {field: Bytes, percentile: 50, gte: 12.5}`. `percentile` is a separate integer selector from 0 through 100; fractional selectors and values outside that range fail conversion. The `ValuePercentile` metric selects the exact nearest-rank observation: sort all finite numeric observations, including duplicates, and take one-based rank `max(1, ceil(percentile * population / 100))`. Zero selects the minimum, 100 the maximum, and the 50th percentile of `[1, 2, 100, 200]` is 2. This numeric quantile interpretation follows pySigma's percentile conditions and differs from the Sigma specification's categorical-frequency example. All six comparisons and fractional thresholds are supported. Finite zero remains an observation; text, missing values, NaN, and infinities do not. Their otherwise valid matches still anchor evaluation, but a window without numeric observations has no defined percentile and satisfies no comparison, including `neq`.

Exact percentile queries sort the complete per-window observation stream and retain the selected numeric type, including adjacent large integers. They use neither the engine's approximate percentile aggregate nor capped arrays, sampling, or observation truncation. Dense sliding groups can create quadratic candidate joins; sorting adds CPU and memory costs. Ranked observations use Kusto's shared 5 GB per-node materialization cache, and engine resource exhaustion fails the query. The runtime fixtures validate results on the recorded engine version; no production throughput or capacity claim is made. Scalar `ValuePercentile` metrics can feed other correlations once per child summary at its `WindowEnd`, and percentile parents can consume other available scalar child metrics.

To require several fields, use a list such as `field: [User, Target]`. Each field's exact count must satisfy the same comparison and threshold in the same window; the comparisons are combined with **AND**. Two users accessing one target fail `gte: 2`, while two users accessing two targets can pass. Missing values are ignored separately for each field, retaining anchors even when every counted value is missing. The result returns `ValueCount_User` and `ValueCount_Target`. In general, multiple fields return `ValueCount_<field>` using the logical condition field name (including an alias name), with quoting for names containing spaces or punctuation. Counted field names must be nonempty and unique.

For `value_avg`, use the same measured-field condition with `type: value_avg`. The `ValueAvg` real metric averages every finite numeric source match, preserving repeated observations, zero, and negatives. Missing values, text (including numeric strings), NaN, and infinities do not enter the average, but valid source matches still establish evaluation timestamps. An anchored window with no numeric observations has an undefined average and satisfies no comparison, including `neq`. Kusto returns NaN for an empty `avgif` population, so generated queries explicitly require at least one numeric observation before comparing. Integer observations also enter floating-point arithmetic: averages do not preserve exact integer precision above 2^53. Comparisons preserve fractional thresholds and use ordinary floating-point results directly, with no tolerance; the average of 0.1 and 0.2 can differ from 0.15. Average parents consume scalar child metrics once per child summary at its `WindowEnd`; average children expose `ValueAvg` to parents. Retained context values require the later retained-context stage. The average fixtures have passed real-engine execution; see [runtime evidence](tests/fixtures/correlation/RUNTIME.md).

For unordered `temporal`, omit the condition to require every referenced source-rule type:

```yaml
correlation:
  type: temporal
  rules: [first-rule, second-rule, third-rule]
  group-by: [DeviceName]
  timespan: 5m
```

The returned `RuleCount` is the exact number of distinct matching source-rule types in the inclusive window. Repeated matches or searches from one rule count as one type. Repeated references to the same source, including references using its name and ID, also represent one type. An explicit condition applies any of the same six comparisons to `RuleCount`; for example, adding `condition: {gte: 2}` to the three-rule example accepts any two types without requiring the third. A type with no matches contributes nothing, and `eq: 0` cannot create an empty-window evaluation.

Unordered temporal correlations accept any event order and tied timestamps. Every contributing combination must fit within one complete timespan: A at 12:00, B at 12:04, and C at 12:08 cannot satisfy a five-minute default-all correlation. Several qualifying combinations ending at one timestamp in the same group return one summary; later qualifying event timestamps retain their own overlapping summaries.

Boolean `temporal` conditions accept `and`, `or`, `not`, and parentheses through the standard Sigma parser. References can be inferred from the expression or listed explicitly in `rules` (the lists must agree):

```yaml
correlation:
  type: temporal
  condition: A and B and not C
  group-by:
    - Computer
  timespan: 5m
```

Presence is evaluated within the same group and inclusive trailing window. Repeated matches do not change presence. `not` binds before `and`, which binds before `or`; `(A and B) or C` can match on C alone. Every referenced source, including a negated source, establishes evaluation timestamps. If C at 12:00, A at 12:01, and B at 12:02 fail a five-minute `A and B and not C` condition, C expiring creates no evaluation: another referenced source match is required. Empty streams produce no summaries. `RuleCount` reports all distinct referenced source types present in the qualifying window, including types in other alternatives. Boolean rules support detection and child sources, aliases, and existing generation controls; child matches use `WindowEnd`.

The upstream expression parser accepts identifier-style source names (letters or underscore first, then letters, digits, or underscores); use `name` references rather than UUIDs or hyphenated names in expressions. Invalid syntax and unresolved references raise public parser errors.

Boolean expressions apply to unordered `temporal` correlations. `temporal_ordered` continues to require a full numeric sequence condition. Requested context comes from positive matches in every satisfied alternative. For `(A and B) or C`, A and C with B absent retain only C; when both alternatives succeed, A, B, and C supply context. Negation follows parser precedence and ordinary Boolean logic, including nested negation; satisfied absence tests supply no values. An absence-only success retains empty context collections without dropping the summary. Context requests leave grouping, evaluation times, `RuleCount`, and generation unchanged. Existing numeric temporal conditions retain their distinct-rule-count meaning.

For `temporal_ordered`, the declared list defines the complete sequence:

```yaml
correlation:
  type: temporal_ordered
  rules: [first-rule, second-rule, third-rule]
  group-by: [DeviceName]
  timespan: 5m
```

Every step must have a **strictly later timestamp** than its predecessor. Equal timestamps cannot establish order. The first and final steps must fit within the inclusive timespan; checking adjacent gaps is insufficient. Intervening and repeated matches do not reset valid alternatives. Results occur at final-step completion timestamps, with one summary per group/completion timestamp even when many event combinations qualify. Later completions retain their overlapping summaries; a later match of an earlier step alone does not create another completion.

The initial ordered implementation requires the full declared sequence. Omit `condition`, or use a comparison equivalent to requiring all distinct source-rule types: with three types, `gte: 3`, `eq: 3`, and `gt: 2` are equivalent. Other thresholds raise an actionable conversion error because partial ordered subsets have no defined interpretation. With one type, `neq: 0` is also equivalent. Repeated list positions, including the same source referenced by name and ID, require separate matches at strictly increasing timestamps; for example, `[first-rule, first-rule, second-rule]` requires two successive first-rule matches followed by second-rule. `RuleCount` still reports the distinct type count (two in that example), so explicit conditions use that count. An omitted condition always requires every declared position.

Every qualifying evaluation returns `WindowStart`, `WindowEnd`, the requested grouping fields, and `EventCount`, `ValueCount`, `ValueSum`, `ValueAvg`, `ValuePercentile`, the per-field value counts, or temporal `RuleCount`. `WindowStart` is `WindowEnd - timespan`. Each joint set of grouping values is independent; omitting `group-by` defines one group. Overlapping qualifying windows remain separate summaries. No evaluation is generated solely because an event expires, and count conditions do not invent empty-window results. Invalid timestamps and null or empty grouping values are excluded before establishing evaluation timestamps. Group names `WindowStart`, `WindowEnd`, and the returned count metrics are rejected because they conflict with the result columns.

All seven supported correlation types (`event_count`, `value_count`, `value_sum`, `value_avg`, `value_percentile`, `temporal`, and `temporal_ordered`) can produce child summaries and consume them, including mixed direct detection sources and multiple correlation levels. Ordered parents retain the full-sequence condition restriction described above. For example, an `event_count` parent can consume an `event_count` child alongside a direct detection:

```yaml
title: Child count
name: child-count
correlation:
  type: event_count
  rules: [source-rule]
  group-by: [User]
  timespan: 5m
  condition: {gte: 10}
---
title: Parent count
correlation:
  type: event_count
  rules: [child-count, direct-rule]
  group-by: [User]
  timespan: 2m
  condition: {gte: 2}
```

Include the referenced detection rules in the same collection. Each qualifying child summary contributes **one** parent source match at the child's `WindowEnd`, even when the child reports `EventCount = 10`. The parent applies its own grouping, timespan, and comparison. Overlapping child summaries remain separate matches; distinct child groups can combine when the parent groups by fewer fields.

Each child's returned grouping fields, `WindowStart`, `WindowEnd`, and actual metrics are available as input fields:

| Child type | Returned metrics |
| --- | --- |
| `event_count` | `EventCount` |
| `value_count`, one counted field | `ValueCount` |
| `value_count`, multiple counted fields | `ValueCount_<logical field>` for each field |
| `value_sum` | `ValueSum` |
| `value_avg` | `ValueAvg` |
| `value_percentile` | `ValuePercentile` |
| `temporal`, `temporal_ordered` | `RuleCount` |

These fields use the child's logical result names, without reapplying detection-table mappings. Parent aliases can map to those names; for example, `ChildSize: {child-count: EventCount}` makes the child metric available as a grouping field named `ChildSize`. Window fields can likewise be aliased to avoid conflicting with the parent's result column names. Conversion rejects required fields absent from the child's result schema, including original detection fields discarded by aggregation or grouping fields removed by an intermediate correlation. Both child and direct branches follow the same normalization and invalid-input exclusions.

A value-count parent can count a returned metric exactly: `condition: {field: EventCount, gte: 2}` requires at least two distinct child count values. Using `field: [EventCount, Host]` requires both independent exact counts to pass and returns `ValueCount_EventCount` and `ValueCount_Host`. Zero is a valid metric value. A metric never multiplies a child's contribution. Temporal parents count distinct identities of their immediate child and detection sources, regardless of descendant identities or metric sizes. Ordered parents apply strict order to the child summaries' `WindowEnd` timestamps; a tie cannot complete the next step, and the entire immediate sequence must fit within the parent's timespan.

Each correlation's `generate` controls standalone output of its immediate referenced sources: the parent's setting controls child output, and the child's setting controls its own sources' output. Suppressed intermediate queries remain available within consumers. This applies at every level, including shared children; following pySigma behavior, a consumer with `generate: false` suppresses standalone output even when another consumer has `generate: true`.

Normalized columns and window intermediates use names beginning with `_sigma_`, with numeric suffixes to avoid known source names. Known names include detection fields, a rule's `fields` list, correlation fields and aliases, table field mappings, the pipeline's schema, and source-table names throughout a chain. Complete child queries use scoped functions so their internal bindings remain local. The built-in pipelines provide their schema through `vars["kusto_schema"]`. Custom pipelines can provide a `BaseSchema` there or list additional source columns in the source rules' `fields`; columns absent from those inputs cannot be discovered during offline conversion. Source columns and rule identity remain available during source assembly; the final aggregate returns only the window, groups, and counts.

The caller owns time-range selection, scheduling, and filtering the reported results. Conversion adds no implicit time filter. Supply at least one full timespan of preceding source history before the first reportable window end, then filter the summaries to the reporting range. Without that history, early windows may have incomplete counts. Timespans must be positive. Sigma weeks, months, and years use pySigma's fixed second lengths, rather than calendar intervals.

For chains, supply the accumulated lookback along each dependency path. The five-minute child and two-minute parent above require seven minutes of preceding detection-source history before the first reportable parent window end; a direct source used only by the parent needs two minutes. Keep the necessary child summaries available, then filter the final parent results to the reporting range. Conversion does not add or expand time filters.

For a concrete three-level example, [the multilevel fixture](tests/fixtures/correlation/chained_multilevel.kql) uses a two-minute event-count child, a three-minute value-count middle, and a four-minute ordered parent with a direct detection branch. Raw events at 11:58 and 12:00 produce a child count of two at 12:00; three raw events at 12:03 produce a child count of three. The middle sees the distinct metrics `{2, 3}` in its inclusive 12:00–12:03 window. A direct event at 12:07 completes the parent's sequence exactly four minutes later. The final result depends on the raw 11:58 event: the longest dependency path requires **2 + 3 + 4 = 9 minutes** of history, while the direct branch requires four minutes. A direct event tied at 12:03 or one tick beyond 12:07 does not complete that sequence.

This changes two previous behaviors: sliding windows replace fixed non-overlapping bins, and several searches from one source rule now feed one aggregate instead of separate aggregates. Ordinary detection queries and pySigma's `generate` control for standalone source output retain their behavior. A count condition must still contain exactly one comparison; range conditions remain unsupported.

The exact event-count baseline preaggregates matches by timestamp and group, pairs candidate contributions using explicit inner joins and bins, applies the actual inclusive timestamp predicate, and sums the integer weights. Value counts first assign scalar identities to native typed values, then apply exact `count_distinctif` over the whole window. Unordered temporal correlations retain source-rule identities and use the same exact aggregation to count types over the whole window. [Kusto documents this function as preview](https://learn.microsoft.com/en-us/kusto/query/count-distinctif-aggregation-function); exceeding its 100-million-unique-value limit raises an engine error. Ordered temporal correlations instead extend valid prefixes one declared step at a time with explicit inner joins, strict timestamp comparisons, and an inclusive total-span bound. For each group and prefix-end timestamp, only the latest viable start is needed: an earlier start has the same future ordering constraints and expires sooner. This collapses alternatives after each step while preserving every possible completion. Bins prune candidates; they do not determine window membership. Dense groups, especially ungrouped streams, can create quadratic intermediate joins. There are no silent caps, sampling, or truncation, and throughput has not been benchmarked. [Deterministic KQL fixtures](tests/fixtures/correlation/README.md) provide worked expected summaries and execution instructions; they are prepared fixtures, not evidence of live engine execution.

Counted-value normalization uses `pack_array(gettype(field), field)` to retain runtime types and values. Numeric `1`, real `1.0`, and string `"1"` remain separate. An iterative depth-first traversal compares typed leaves at exact paths: arrays retain element order, property bags compare by their mappings, and nested datetime/timespan/GUID values remain distinct from strings. Repeated real NaNs and signed zeros compare equal, including inside containers. Null and empty strings receive no counted identity while their rows still establish anchors; zero and empty arrays/bags remain values. Each field receives its own identities. JSON measures traversal work and encodes string bag keys; it never decides value equality. The query checks the engine's range limit and completed traversal explicitly, so excessive input fails rather than silently truncating. A self-join within each group chooses the smallest stable row ID among equivalent values. IDs are labels whose representative row may lie outside a later window. Traversal and joins add substantial work and can create quadratic intermediate results over supplied history. Normalized rows and traversal are cached with [`materialize()`](https://learn.microsoft.com/en-us/kusto/query/materialize-function); its shared 5 GB per-node cache limit aborts on exhaustion. No production throughput or capacity benchmark is claimed.

### Retained investigation context

Fields requested through the source or correlation rule's top-level `fields` are combined and deduplicated for `event_count`, `value_count`, `value_sum`, `value_avg`, `value_percentile`, and ordinary unordered `temporal` summaries. Each requested nongrouping field returns an unordered collection of distinct runtime-typed values. A requested grouping field stays scalar. Context does not split groups, multiply measurements, or create additional summaries; it comes from the same valid source matches inside each inclusive qualifying window. Nested arrays and bags preserve typed leaves, bag key order does not affect distinctness, array positions do, and repeated NaNs and signed zero compare consistently.

Logical field names are preserved through available source mappings and per-source correlation aliases. A context name colliding with `WindowStart`, `WindowEnd`, or the result metric fails conversion. Resolve it explicitly, for example `fields: [OriginalWindowEnd]` with `aliases: {OriginalWindowEnd: {source-rule: WindowEnd}}` inside `correlation`. The query also returns an allocated `_sigma_context_rows` column containing observed tuples of boxed `[runtime type, value]` fields. This preserves relationships such as `(Alice, server1)` and `(Bob, server2)` independently of their displayed collections; it is internal composition data and its allocated name can vary.

Context collection uses complete typed identity traversal and checked collection lengths. Engine traversal, collection, memory, or query limits fail explicitly; values are never silently truncated to fit. The exact implementation can be expensive and no throughput claim is made. Boolean context preserves typed deduplication and observed field relationships from every satisfied alternative. Ordered participating-context membership and parents consuming individual retained values remain follow-up work; ordered membership requests currently fail clearly. Scalar metric chaining retains its existing behavior. [The retained-context runtime fixtures](tests/fixtures/correlation/RUNTIME.md) record successful execution of all six delivered summary types and nested typed values.

### 🔄 Pipeline & Backend Args (New in 0.2.0)

For the `microsoft_xdr_pipeline`:

- `transform_parent_image`: Controls ParentImage field mapping behavior
  - When set to `True` (default), maps ParentImage to InitiatingProcessParentFileName
  - When set to `False`, maps ParentImage to InitiatingProcessFileName
  - Useful for adjusting field mappings based on specific rule requirements
  - Example usage:

```python
from sigma.pipelines.microsoftxdr import microsoft_xdr_pipeline
pipeline = microsoft_xdr_pipeline(transform_parent_image=False)
```

This argument allows fine-tuning of the ParentImage field mapping, which can be crucial for accurate rule conversion in certain scenarios. By default, it follows the behavior of mapping ParentImage to the parent process name, but setting it to `False` allows for mapping to the initiating process name instead.

### 🗃️ Custom Table Names (New in 0.3.0) (Beta)

The `query_table` argument allows users to override table mappings and set custom table names.  This is useful for converting Sigma rules where the rule category does not easily map to the default table names.

#### YAML Pipelines

To set a custom table name, ensure your pipeline has a priority of 9 or lower, as sigma-cli merges pipelines based on priority (default is 10). Field mappings in `mappings.py` will apply according to your specified table name, along with any custom field mapping transformations.

```YAML
# test_table_name_pipeline.yml
name: Custom Query Table Pipeline
priority: 1
transformations:
- id: test_name_name
  type: set_state
  key: "query_table"
  val: ["DeviceProcessEvents"]
```

```bash
sigma convert -t kusto -p microsoft_xdr -p test_table_name_pipeline.yml test_rule.yml
```

#### Python Pipelines

You can also set the table name in the pipeline via Python by passing the `query_table` parameter to the pipeline.

```python
from sigma.pipelines.microsoftxdr import microsoft_xdr_pipeline
my_pipeline = microsoft_xdr_pipeline(query_table="DeviceProcessEvents")
```

## 🔄 Processing Pipelines

This project includes three main processing pipelines, each designed for a specific Microsoft product:

1. **Microsoft XDR Pipeline** (formerly Microsoft 365 Defender)
   - Status: Production-ready
   - Supports a wide range of Sigma rule categories
   - All tables supported, but additional field mapping contributions welcome

2. **Sentinel ASIM Pipeline**
   - Status: Beta
   - Transforms rules for Microsoft Sentinel Advanced Security Information Model (ASIM)
   - All tables supported, but field mappings are limited

3. **Azure Monitor Pipeline**
   - Status: Alpha
   - Currently supports field mappings for `SecurityEvent` and `SigninLogs` tables only
   - All tables supported, but requires custom field mappings for other tables

Each pipeline includes a `query_table` parameter for setting custom table names.

### 📊 Rule Support

Rules are supported if either:

- A valid table name is supplied via the `query_table` parameter or YAML pipeline
- The rule's logsource category is supported and mapped in the pipeline's `mappings.py` file
- The rule has an `EventID` or `EventCode` field in the `detection` section, and the eventid is present in the pipeline's `eventid_to_table_mappings` dictionary

### 🖥️ Commonly Supported Categories

- process_creation
- image_load
- network_connection
- file_access, file_change, file_delete, file_event, file_rename
- registry_add, registry_delete, registry_event, registry_set

Specific pipelines may support additional categories. Check each pipeline's `mappings.py` file for details.

## 🧪 Custom Transformations

This package includes several custom `ProcessingPipeline` `Transformation` classes:

1. **DynamicFieldMappingTransformation**
   - Determines field mappings based on the `query_table` state parameter

2. **GenericFieldMappingTransformation**
   - Applies common field mappings across all tables in a pipeline

3. **BaseHashesValuesTransformation**
   - Transforms the Hashes field, removing hash algorithm prefixes

4. **ParentImageValueTransformation**
   - Extracts parent process name from Sysmon ParentImage field

5. **SplitDomainUserTransformation**
   - Splits User field into separate domain and username fields

6. **RegistryActionTypeValueTransformation**
   - Adjusts registry ActionType values for compatibility

7. **InvalidFieldTransformation**
   - Identifies unsupported or invalid fields in rules

8. **SetQueryTableStateTransformation**
   - Manages the `query_table` state based on rule category or custom settings

### 📊 Custom Postprocessing Item

1. **PrependQueryTablePostprocessingItem**

- Adds table name as prefix to each query in a SigmaCollection, or single query in a SigmaRule

## ❓Frequently Asked Questions

### How do I set the table name for a rule?

You can set the table name for a rule by adding the `query_table` parameter to the pipeline and setting it to the table name you want to use.

```python
from sigma.pipelines.microsoftxdr import microsoft_xdr_pipeline
pipeline = microsoft_xdr_pipeline(query_table="DeviceProcessEvents")
```

### How do I set the table name for a rule in YAML?

You can set the table name for a rule in YAML by adding the `query_table` parameter to the pipeline and setting it to the table name you want to use.

```YAML
# test_table_name_pipeline.yml
name: 
priority: 1
transformations:
- id: test_name_name
  type: set_state
  key: "query_table"
  val: ["DeviceProcessEvents"]
```

```bash
sigma convert -t kusto -p microsoft_xdr -p test_table_name_pipeline.yml test_rule.yml
```

### How is the table name determined for a rule?

The table name is set by the `SetQueryTableStateTransformation` transformation, which is the first transformation in each pipeline. The `query_table` is set to the pipeline's `state` parameter with the following priority:
1. The `query_table` parameter passed to the pipeline, if using a Python script/code.
2. The `query_table` parameter passed to the pipeline in a custom YAML pipeline, if using sigma-cli.
3. The `logsource.category` field in the rule, if the category is present in the pipeline's `category_to_table_mappings` dictionary.
4. The `EventID` or `EventCode` field, if present in the rule's `detection` section, and if the eventid is present in the pipeline's `eventid_to_table_mappings` dictionary.
5. If none of the above are present, an error is raised.

### How are field mappings determined for a rule?

The field mappings are determined by the `DynamicFieldMappingTransformation` transformation. It will use the table name from the pipeline state's `query_table` key.  The field mapping logic is defined in each pipeline's `mappings.py` file for each table.  If a field is not found in the table, the `GenericFieldMappingTransformation` will apply generic field mappings.  If a field is not found in the generic field mappings, the field will be kept the same.

### What tables are supported for each pipeline?

The tables that are supported for each pipeline are defined in each pipeline's `tables.py` file. This file is automatically generated by the scripts in the `utils` folder. These scripts pull documentation from Microsoft to get all documented tables and their fields and schema.

### I am receiving an `Invalid SigmaDetectionItem field name encountered` error. What does this mean?

This error means that the field name(s) provided in the error are not found in the tables fields defined in `tables.py` for the pipeline you are using. This probably means that a Sigma rule's field was not found in the field mappings for the table.  To fix this error, you can supply your own custom field mappings to convert the unsupported field into a supported one. For example, in using YAML:

```YAML
# custom_field_mapping_pipeline.yml
name: Custom Field Mapping
priority: 1
transformations:
- id: field_mapping
    type: field_name_mapping
    mapping:
        MyNotSupportedField: a_supported_field
    rule_conditions:
    - type: logsource
        service: sysmon
```

```bash
sigma convert -t kusto -p custom_field_mapping_pipeline.yml -p microsoft_xdr test_rule.yml
```

If you find the field mapping useful, please consider submitting a PR to add it to the pipeline's field mappings :)

### My query_table or custom field mapping isn't working

Each pipeline in the project has a priority of 10. If you are trying to set the table name or custom field mappings, your pipeline needs to have a priority of 9 or less.  You can set the priority in the YAML pipeline like so:

```YAML
# test_table_name_pipeline.yml
name:
priority: 9
transformations:
- id: test_name_name
  type: set_state
  key: "query_table"
  val: ["DeviceProcessEvents"]
```

## 👨‍💻 Developer Guide

### Factory Pattern for ProcessingItems (v1.0+)

**Background**: pySigma v1.0.0 introduced [Breaking Change #12](https://github.com/SigmaHQ/pySigma/blob/main/docs/Breaking_Changes.rst#12-processingitem-reference-assignment), which states that `ProcessingItem`, `Transformation`, and condition class objects can only reference one pipeline instance. Reusing these objects across multiple pipeline instances causes errors.

**Solution**: This project uses the **factory pattern** to create fresh `ProcessingItem` instances for each pipeline.

#### Implementation

Instead of creating module-level `ProcessingItem` singletons (old v0.x pattern), we use factory functions that return new instances:

```python
# ❌ OLD (v0.x) - Module-level singleton (causes issues in v1.0+)
drop_fields_proc_item = ProcessingItem(
    identifier="azure_monitor_drop_fields",
    transformation=DropDetectionItemTransformation(),
    field_name_conditions=[IncludeFieldCondition(["ObjectType"])],
)

# ✅ NEW (v1.0+) - Factory function
def _create_drop_fields_item():
    """Drop ObjectType fields"""
    return ProcessingItem(
        identifier="azure_monitor_drop_fields",
        transformation=DropDetectionItemTransformation(),
        field_name_conditions=[IncludeFieldCondition(["ObjectType"])],
    )
```

#### Pipeline Construction

Pipeline functions call factory functions to build fresh processing items:

```python
def microsoft_xdr_pipeline(query_table: Optional[str] = None) -> ProcessingPipeline:
    """Creates a new Microsoft XDR pipeline with fresh ProcessingItems."""
    return ProcessingPipeline(
        name="Microsoft XDR Pipeline",
        priority=10,
        items=[
            _create_set_query_table_item(query_table),  # Factory call
            _create_drop_eventid_item(),                 # Factory call
            _create_fieldmappings_item(),                # Factory call
            # ... more factory calls
        ],
    )
```

#### Guidelines for Contributors

When adding new `ProcessingItem` instances to pipelines:

1. **Create a factory function** with a descriptive name starting with `_create_`
2. **Add a docstring** explaining what the processing item does
3. **Return a new ProcessingItem instance** each time the function is called
4. **Call the factory function** in the main pipeline function
5. **Never reuse** `ProcessingItem` objects across pipeline calls

**Example**:

```python
def _create_my_custom_transformation_item():
    """Applies custom field transformation for XYZ table."""
    return ProcessingItem(
        identifier="my_custom_transformation",
        transformation=MyCustomTransformation(),
        rule_conditions=[LogsourceCondition(category="my_category")],
    )

def my_pipeline() -> ProcessingPipeline:
    return ProcessingPipeline(
        name="My Pipeline",
        priority=10,
        items=[
            _create_my_custom_transformation_item(),  # Fresh instance
        ],
    )
```

This pattern ensures:
- ✅ Each pipeline instance gets fresh `ProcessingItem` objects
- ✅ Multiple backend instances can coexist without conflicts
- ✅ State management works correctly across rule conversions
- ✅ Tests can create multiple pipeline instances without issues

For more details, see the [pySigma v1.0.0 Breaking Changes documentation](https://github.com/SigmaHQ/pySigma/blob/main/docs/Breaking_Changes.rst).

## 🤝 Contributing

Contributions are welcome, especially for table and field mappings! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

Please make sure to update tests as appropriate.

## 📄 License

This project is licensed under the GNU Lesser General Public License v3.0 - see the [LICENSE](LICENSE) file for details.
