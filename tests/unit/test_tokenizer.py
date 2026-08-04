from datetime import datetime, timedelta, timezone

import pytest

from blindfold.core.tokenizer import (
    SchemaField,
    describe_schema,
    tokenize_result,
    validate_path,
    _resolve_paths,
)
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

# --- describe_schema ------------------------------------------------------


def test_describe_schema_none_when_no_fields():
    assert describe_schema([]) is None


def test_describe_schema_reports_path_semantic_type_and_unit():
    note = describe_schema(
        [SchemaField(path="$.salary", semantic_type="salary", unit="EUR/year")]
    )
    assert "$.salary" in note
    assert "salary" in note
    assert "EUR/year" in note


def test_describe_schema_handles_field_without_metadata():
    note = describe_schema([SchemaField(path="$.secret")])
    assert "$.secret" in note
    assert note.rstrip().endswith("$.secret")


def test_describe_schema_is_one_line_per_field():
    note = describe_schema(
        [SchemaField(path=f"$.f{i}", semantic_type="x") for i in range(4)]
    )
    assert sum(1 for line in note.splitlines() if line.startswith("  ")) == 4


def test_describe_schema_mentions_the_compute_tool():
    note = describe_schema([SchemaField(path="$.salary", semantic_type="salary")])
    assert "blindfold_compute" in note


def test_describe_schema_public_from_top_level_module():
    from blindfold import describe_schema as top_level

    assert top_level is describe_schema


# --- path validation ------------------------------------------------------
#
# Unsupported syntax used to be reinterpreted instead of refused: `$..salary`
# quietly became `$.salary`, matching a top-level field, missing every nested
# one, and still minting tokens so the config looked correct.


@pytest.mark.parametrize(
    "path",
    [
        "$.salary",
        "$.a.b.c",
        "$.employees[*].salary",
        "$.a[*].b[*].c",  # wildcards nest
        "$.items[0].name",  # integer index
        "$",  # whole payload
        "$.a[*]",  # wildcard last
    ],
)
def test_validate_path_accepts_supported_dialect(path):
    validate_path(path)  # must not raise


@pytest.mark.parametrize(
    "path, expected_in_message",
    [
        ("$..salary", "recursive descent"),
        ("$.a..b", "recursive descent"),
        ("$.items[?(@.type == 'x')].n", "unsupported subscript"),
        ("$.items[0:5].n", "unsupported subscript"),
        ("$.items['name']", "unsupported subscript"),
        ("$.items[*", "unbalanced"),
        ("$.a.", "ends with"),
        ("salary", "must start with '$'"),
    ],
)
def test_validate_path_rejects_unsupported_syntax(path, expected_in_message):
    with pytest.raises(ValueError) as ei:
        validate_path(path)
    assert expected_in_message in str(ei.value)


def test_recursive_descent_message_names_what_it_would_have_meant():
    # The danger is silence, so the error says what the path was being read as.
    with pytest.raises(ValueError) as ei:
        validate_path("$..salary")
    assert "$.salary" in str(ei.value)


def test_schema_field_rejects_bad_path_at_construction():
    # Mode B builds SchemaField directly, without going through the YAML.
    with pytest.raises(ValueError):
        SchemaField(path="$..salary", semantic_type="salary")


def test_a_path_that_simply_does_not_match_is_still_a_silent_no_op():
    # Defensive declaration must stay free: "did not match this response" is
    # not the same as "could never match anything".
    store = MemoryTokenStore()
    payload = {"name": "Andrea"}
    result = tokenize_result(
        payload, "hr.get", [SchemaField(path="$.absent.deeply")], store, "s", TTL
    )
    assert result == payload
    assert store.find_by_session("s") == []
