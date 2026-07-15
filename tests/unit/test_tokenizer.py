from datetime import datetime, timedelta, timezone

from blindfold.core.tokenizer import SchemaField, tokenize_result, _resolve_paths
from blindfold.core.vault import MemoryTokenStore

TTL = datetime.now(tz=timezone.utc) + timedelta(hours=1)
TOKEN_RE = r"⟦tok_[0-9a-f]{8}⟧"


def test_resolve_static_path():
    payload = {"salary": 50000}
    assert _resolve_paths(payload, "$.salary") == [(["salary"], 50000)]


def test_resolve_nested_path():
    payload = {"person": {"salary": 50000}}
    assert _resolve_paths(payload, "$.person.salary") == [(["person", "salary"], 50000)]


def test_resolve_wildcard_over_list():
    payload = {"items": [{"name": "a"}, {"name": "b"}]}
    result = _resolve_paths(payload, "$.items[*].name")
    assert result == [
        (["items", 0, "name"], "a"),
        (["items", 1, "name"], "b"),
    ]


def test_resolve_missing_path_yields_empty():
    payload = {"salary": 50000}
    assert _resolve_paths(payload, "$.does.not.exist") == []


def test_tokenize_static_field_replaces_value_and_stores_record():
    import re

    store = MemoryTokenStore()
    payload = {"name": "Alice", "salary": 50000}
    fields = [SchemaField(path="$.salary", semantic_type="salary", unit="EUR/year")]

    result = tokenize_result(payload, "hr.get_salary", fields, store, "s", TTL)

    assert result["name"] == "Alice"
    assert re.fullmatch(TOKEN_RE, result["salary"])
    records = store.find_by_session("s")
    assert len(records) == 1
    rec = records[0]
    assert rec.value == 50000
    assert rec.dtype == "number"
    assert rec.semantic_type == "salary"
    assert rec.unit == "EUR/year"
    assert rec.lineage.op == "tool_result"
    assert rec.lineage.tool == "hr.get_salary"
    assert rec.lineage.path == "$.salary"


def test_tokenize_wildcard_mints_one_per_match():
    store = MemoryTokenStore()
    payload = {"people": [{"salary": 10}, {"salary": 20}, {"salary": 30}]}
    fields = [SchemaField(path="$.people[*].salary", semantic_type="salary")]

    result = tokenize_result(payload, "hr.list", fields, store, "s", TTL)

    assert all(isinstance(p["salary"], str) for p in result["people"])
    assert {r.value for r in store.find_by_session("s")} == {10, 20, 30}


def test_tokenize_missing_paths_no_op():
    store = MemoryTokenStore()
    payload = {"name": "Alice"}
    fields = [SchemaField(path="$.salary")]

    result = tokenize_result(payload, "hr.get_salary", fields, store, "s", TTL)

    assert result == payload
    assert store.find_by_session("s") == []


def test_tokenize_non_scalar_uses_object_dtype():
    store = MemoryTokenStore()
    payload = {"address": {"street": "1 rd", "city": "X"}}
    fields = [SchemaField(path="$.address")]

    tokenize_result(payload, "hr.get_address", fields, store, "s", TTL)

    rec = store.find_by_session("s")[0]
    assert rec.dtype == "object"
    assert rec.value == {"street": "1 rd", "city": "X"}


def test_tokenize_boolean_dtype():
    store = MemoryTokenStore()
    payload = {"is_manager": True}
    fields = [SchemaField(path="$.is_manager")]
    tokenize_result(payload, "hr.get_role", fields, store, "s", TTL)
    assert store.find_by_session("s")[0].dtype == "boolean"


def test_tokenize_deep_copies_payload():
    store = MemoryTokenStore()
    payload = {"salary": 50000, "nested": {"k": "v"}}
    fields = [SchemaField(path="$.salary")]

    result = tokenize_result(payload, "hr.get_salary", fields, store, "s", TTL)

    result["nested"]["k"] = "MUTATED"
    assert payload["nested"]["k"] == "v"


def test_tokenize_no_fields_still_returns_copy():
    store = MemoryTokenStore()
    payload = {"name": "Alice"}
    result = tokenize_result(payload, "hr.get_name", [], store, "s", TTL)
    assert result == payload
    assert result is not payload
