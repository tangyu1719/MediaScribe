# Structured query extension

This extension combines natural-language intent parsing with deterministic field filtering.

The intended flow is:

1. Build a `FormSchema` from an allow-listed table or form definition.
2. Call `FormSchemaCache.ensure()` on first use. The cache is refreshed when the schema fingerprint changes, with a seven-day fallback refresh.
3. Let a rule adapter and optional LLM adapter return a structured plan. The adapter never returns executable SQL.
4. Validate fields, operators, sorting, and values against the active schema.
5. Render the validated plan as editable advanced-search controls.
6. Compile only the validated filters through `SafeSqlCompiler`, then append the generated fragment to application-owned SQL with bound parameters.

This is intentionally a hybrid rather than unrestricted Text-to-SQL. Existing hard-coded scope, authorization, joins, pagination, and ordering remain owned by the application. AI handles language understanding and field mapping only.

```python
fields = fields_from_sqlite(
    conn,
    "messages",
    labels={"sender_name": "发送者", "sent_at": "发送时间"},
    aliases={"sender_name": ("发件人", "成员")},
    allowed_fields=("sender_name", "sent_at", "content"),
)
schema, cache_status = FormSchemaCache(cache_path).ensure(
    FormSchema("messages", "messages", "消息", fields)
)
plan = StructuredQueryPlanner(rule_adapter=rules, llm_adapter=llm).plan(
    "昨天张三发的图片",
    schema,
    use_llm=True,
)
clauses, params = SafeSqlCompiler({
    "sender_name": "m.sender_name",
    "sent_at": "m.sent_at",
    "content": "m.content",
}).compile_where(plan)
```

Do not expose raw table metadata, secrets, unrestricted columns, or generated SQL to the LLM. Always keep authorization and row scope outside the planner.

## Design references

- [Vanna](https://github.com/vanna-ai/vanna) demonstrates structured UI responses, user-aware execution, row-level security, audit hooks, and a natural-language-to-SQL workflow.
- [Dataherald](https://github.com/Dataherald/dataherald) separates the NL-to-SQL engine, enterprise authorization layer, and administration/observability surface.
- [DB-GPT](https://github.com/eosphoros-ai/DB-GPT) combines SQL, code, reusable skills, multiple data sources, and sandboxed execution.

This SDK intentionally keeps the useful metadata and planning ideas while avoiding unrestricted SQL generation for ordinary product search. A validated field plan is easier to show, edit, cache, audit, and combine with existing hard-coded filters.
