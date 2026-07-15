from datetime import datetime, timedelta, timezone

from blindfold.core.lineage import (
    Lineage,
    Policy,
    VaultRecord,
    compose_policy,
    compose_ttl,
)


def _record(token: str, *, reveal: bool = True, compute: bool = True, ttl_min: int = 60) -> VaultRecord:
    return VaultRecord(
        token=token,
        value="v",
        dtype="string",
        semantic_type=None,
        unit=None,
        session_id="s",
        created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        ttl=datetime(2026, 7, 15, tzinfo=timezone.utc) + timedelta(minutes=ttl_min),
        lineage=Lineage(op="literal"),
        policy=Policy(reveal_to_frontend=reveal, can_be_input_to_compute=compute),
    )


def test_compose_policy_all_true():
    p = compose_policy([Policy(), Policy()])
    assert p.reveal_to_frontend is True
    assert p.can_be_input_to_compute is True


def test_compose_policy_any_false_reveals_false():
    p = compose_policy(
        [Policy(reveal_to_frontend=True), Policy(reveal_to_frontend=False)]
    )
    assert p.reveal_to_frontend is False


def test_compose_policy_any_false_compute_false():
    p = compose_policy(
        [Policy(can_be_input_to_compute=True), Policy(can_be_input_to_compute=False)]
    )
    assert p.can_be_input_to_compute is False


def test_compose_policy_empty_defaults_to_permissive():
    p = compose_policy([])
    assert p == Policy()


def test_compose_ttl_takes_min():
    a = _record("tok_a", ttl_min=30)
    b = _record("tok_b", ttl_min=90)
    assert compose_ttl([a, b]) == a.ttl


def test_vault_record_is_frozen():
    r = _record("tok_a")
    try:
        r.value = "x"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("expected VaultRecord to be frozen")
