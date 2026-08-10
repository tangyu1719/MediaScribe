"""Reusable natural-language form query planning with safe SQL compilation.

The LLM never returns executable SQL. It may only propose a structured plan,
which is validated against a versioned field schema before hard-coded SQL is
compiled with bound parameters.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class FormField:
    name: str
    label: str
    data_type: str = "text"
    aliases: Sequence[str] = field(default_factory=tuple)
    operators: Sequence[str] = field(default_factory=lambda: ("contains", "eq", "neq"))
    filterable: bool = True

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["aliases"] = list(self.aliases)
        value["operators"] = list(self.operators)
        return value


@dataclass(frozen=True)
class FormSchema:
    schema_id: str
    table_name: str
    label: str
    fields: Sequence[FormField]
    version: str = ""
    synced_at: str = ""
    refresh_after: str = ""

    def with_version(self, *, refresh_days: int = 7, synced_at: str = "") -> "FormSchema":
        now = synced_at or datetime.now().isoformat(timespec="seconds")
        payload = {"schema_id": self.schema_id, "table_name": self.table_name, "fields": [item.to_dict() for item in self.fields]}
        version = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
        refresh_after = (datetime.fromisoformat(now) + timedelta(days=max(1, refresh_days))).isoformat(timespec="seconds")
        return FormSchema(self.schema_id, self.table_name, self.label, self.fields, version, now, refresh_after)

    def to_dict(self) -> Dict[str, Any]:
        return {"schema_id": self.schema_id, "table_name": self.table_name, "label": self.label, "fields": [item.to_dict() for item in self.fields], "version": self.version, "synced_at": self.synced_at, "refresh_after": self.refresh_after}


class FormSchemaCache:
    """Initialize once, detect structural versions, and refresh weekly."""

    def __init__(self, path: Path, *, refresh_days: int = 7) -> None:
        self.path = Path(path)
        self.refresh_days = max(1, int(refresh_days or 7))

    def ensure(self, schema: FormSchema) -> tuple[FormSchema, str]:
        current = schema.with_version(refresh_days=self.refresh_days)
        payload = self._read()
        cached = payload.get(schema.schema_id)
        status = "hit"
        if not isinstance(cached, dict) or cached.get("version") != current.version:
            status = "initialized" if not cached else "schema_changed"
        else:
            synced_at = str(cached.get("synced_at") or "")
            try:
                if datetime.fromisoformat(synced_at) + timedelta(days=self.refresh_days) > datetime.now():
                    return FormSchema(schema.schema_id, schema.table_name, schema.label, schema.fields, current.version, synced_at, str(cached.get("refresh_after") or "")), status
            except ValueError:
                pass
            status = "weekly_refresh"
        payload[schema.schema_id] = current.to_dict()
        self._write(payload)
        return current, status

    def _read(self) -> Dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


@dataclass
class FieldFilter:
    field: str
    operator: str
    value: Any
    source: str = "rule"
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StructuredQueryPlan:
    raw_query: str
    text_query: str = ""
    intent_summary: str = ""
    filters: List[FieldFilter] = field(default_factory=list)
    sort: List[Dict[str, str]] = field(default_factory=list)
    follow_up_question: str = ""
    schema_version: str = ""
    llm_powered: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"raw_query": self.raw_query, "text_query": self.text_query, "intent_summary": self.intent_summary, "filters": [item.to_dict() for item in self.filters], "sort": self.sort, "follow_up_question": self.follow_up_question, "schema_version": self.schema_version, "llm_powered": self.llm_powered}


PlanAdapter = Callable[[str, FormSchema], Mapping[str, Any]]


class StructuredQueryPlanner:
    """Combine deterministic parsing with an optional LLM plan adapter."""

    def __init__(self, *, rule_adapter: Optional[PlanAdapter] = None, llm_adapter: Optional[PlanAdapter] = None) -> None:
        self.rule_adapter = rule_adapter
        self.llm_adapter = llm_adapter

    def plan(self, query: str, schema: FormSchema, *, use_llm: bool = False, explicit_filters: Sequence[Mapping[str, Any]] | None = None) -> StructuredQueryPlan:
        raw = str(query or "").strip()
        base = dict(self.rule_adapter(raw, schema)) if self.rule_adapter else {"text_query": raw, "intent_summary": "field-aware search"}
        powered = False
        if use_llm and self.llm_adapter:
            proposed = dict(self.llm_adapter(raw, schema))
            if proposed:
                base.update({key: value for key, value in proposed.items() if value not in (None, "")})
                powered = True
        filters = self.validate_filters(explicit_filters if explicit_filters is not None else base.get("filters") or [], schema, source="user" if explicit_filters is not None else "ai" if powered else "rule")
        return StructuredQueryPlan(raw_query=raw, text_query=str(base.get("text_query") or "").strip(), intent_summary=str(base.get("intent_summary") or "").strip()[:240], filters=filters, sort=self.validate_sort(base.get("sort") or [], schema), follow_up_question=str(base.get("follow_up_question") or "").strip()[:240], schema_version=schema.version, llm_powered=powered)

    @staticmethod
    def validate_filters(filters: Sequence[Mapping[str, Any]], schema: FormSchema, *, source: str) -> List[FieldFilter]:
        field_map = {item.name: item for item in schema.fields}
        alias_map: Dict[str, str] = {}
        for item in schema.fields:
            for alias in (item.name, item.label, *item.aliases):
                alias_map[str(alias).strip().lower()] = item.name
        result: List[FieldFilter] = []
        for item in filters:
            name = alias_map.get(str(item.get("field") or "").strip().lower(), str(item.get("field") or ""))
            spec = field_map.get(name)
            operator = str(item.get("operator") or "eq")
            value = item.get("value")
            if not spec or not spec.filterable or operator not in spec.operators or value in (None, "", []):
                continue
            result.append(FieldFilter(name, operator, value, source, float(item.get("confidence") or 1.0)))
        return result

    @staticmethod
    def validate_sort(sort: Sequence[Mapping[str, Any]], schema: FormSchema) -> List[Dict[str, str]]:
        allowed = {item.name for item in schema.fields}
        result = []
        for item in sort:
            field_name = str(item.get("field") or "")
            if field_name in allowed:
                result.append({"field": field_name, "direction": "asc" if str(item.get("direction") or "").lower() == "asc" else "desc"})
        return result[:5]


class SafeSqlCompiler:
    """Compile a validated plan to SQL fragments using a fixed column map."""

    def __init__(self, column_map: Mapping[str, str]) -> None:
        self.column_map = dict(column_map)

    def compile_where(self, plan: StructuredQueryPlan) -> tuple[List[str], List[Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        symbols = {"eq": "=", "neq": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
        for item in plan.filters:
            column = self.column_map.get(item.field)
            if not column:
                continue
            if item.operator == "contains":
                clauses.append(f"{column} LIKE ?")
                params.append(f"%{item.value}%")
            elif item.operator == "starts_with":
                clauses.append(f"{column} LIKE ?")
                params.append(f"{item.value}%")
            elif item.operator in symbols:
                clauses.append(f"{column} {symbols[item.operator]} ?")
                params.append(item.value)
            elif item.operator == "between" and isinstance(item.value, list) and len(item.value) == 2:
                clauses.append(f"{column} BETWEEN ? AND ?")
                params.extend(item.value)
        return clauses, params


def fields_from_sqlite(connection, table_name: str, *, labels: Mapping[str, str] | None = None, aliases: Mapping[str, Sequence[str]] | None = None, allowed_fields: Sequence[str] | None = None) -> List[FormField]:
    labels, aliases, allowed = labels or {}, aliases or {}, set(allowed_fields or [])
    escaped = table_name.replace('"', '""')
    rows = connection.execute(f'PRAGMA table_info("{escaped}")').fetchall()
    result: List[FormField] = []
    for row in rows:
        name, raw_type = str(row[1]), str(row[2] or "TEXT").upper()
        if allowed and name not in allowed:
            continue
        data_type = "number" if any(token in raw_type for token in ("INT", "REAL", "NUM", "DEC")) else "datetime" if "DATE" in raw_type or "TIME" in raw_type or name.endswith("_at") or name.endswith("_date") else "text"
        operators = ("eq", "neq", "gt", "gte", "lt", "lte", "between") if data_type in {"number", "datetime"} else ("contains", "eq", "neq", "starts_with")
        result.append(FormField(name, labels.get(name, name), data_type, tuple(aliases.get(name, ())), operators))
    return result
