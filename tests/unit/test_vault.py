from datetime import datetime, timedelta, timezone

from freezegun import freeze_time

from blindfold.core.lineage import Lineage, Policy, VaultRecord
from blindfold.core.vault import MemoryTokenStore

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def _rec(token: str, *, ttl_min: int = 60, inputs: tuple[str, ...] = (), op: str = "literal") -> VaultRecord:
    return VaultRecord(
        token=token,
        value=f"val_of_{token}",
        dtype="string",
        semantic_type=None,
        unit=None,
        session_id="s",
        created_at=NOW,
        ttl=NOW + timedelta(minutes=ttl_min),
        lineage=Lineage(op=op, inputs=inputs),
        policy=Policy(),
    )


def test_mint_token_shape():
    tok = MemoryTokenStore.mint_token()
    assert tok.startswith("⟦tok_")
    assert tok.endswith("⟧")
    hex_part = tok.removeprefix("⟦tok_").removesuffix("⟧")
    assert len(hex_part) == 16
    int(hex_part, 16)  # must parse as hex


def test_mint_token_is_unique_across_calls():
    tokens = {MemoryTokenStore.mint_token() for _ in range(1000)}
    assert len(tokens) == 1000


@freeze_time(NOW)
def test_put_then_get_and_resolve():
    store = MemoryTokenStore()
    r = _rec("⟦tok_00000001⟧")
    store.put(r)
    assert store.get("⟦tok_00000001⟧") is r
    assert store.resolve("⟦tok_00000001⟧") == "val_of_⟦tok_00000001⟧"


def test_get_unknown_returns_none():
    store = MemoryTokenStore()
    assert store.get("⟦tok_deadbeef⟧") is None
    assert store.resolve("⟦tok_deadbeef⟧") is None


@freeze_time(NOW)
def test_find_by_session_isolates():
    store = MemoryTokenStore()
    a = _rec("⟦tok_00000001⟧")
    b = VaultRecord(**{**a.__dict__, "token": "⟦tok_00000002⟧", "session_id": "s2"})
    store.put(a)
    store.put(b)
    assert store.find_by_session("s") == [a]
    assert store.find_by_session("s2") == [b]


def test_ttl_expiry_hides_record_from_get():
    with freeze_time(NOW) as frozen:
        store = MemoryTokenStore()
        store.put(_rec("⟦tok_00000001⟧", ttl_min=1))
        assert store.get("⟦tok_00000001⟧") is not None
        frozen.tick(delta=timedelta(minutes=2))
        assert store.get("⟦tok_00000001⟧") is None
        assert store.resolve("⟦tok_00000001⟧") is None


def test_purge_expired_returns_count():
    with freeze_time(NOW) as frozen:
        store = MemoryTokenStore()
        store.put(_rec("⟦tok_00000001⟧", ttl_min=1))
        store.put(_rec("⟦tok_00000002⟧", ttl_min=100))
        frozen.tick(delta=timedelta(minutes=2))
        assert store.purge_expired() == 1
        assert store.get("⟦tok_00000001⟧") is None
        assert store.get("⟦tok_00000002⟧") is not None


@freeze_time(NOW)
def test_invalidate_cascade_removes_descendants():
    store = MemoryTokenStore()
    a = _rec("⟦tok_00000001⟧")
    b = _rec("⟦tok_00000002⟧")
    c = _rec("⟦tok_00000003⟧", inputs=("⟦tok_00000001⟧",), op="blind_compute")
    d = _rec("⟦tok_00000004⟧", inputs=("⟦tok_00000003⟧",), op="blind_compute")
    e = _rec("⟦tok_00000005⟧", inputs=("⟦tok_00000002⟧",), op="blind_compute")  # unrelated
    for r in (a, b, c, d, e):
        store.put(r)

    removed = store.invalidate_cascade("⟦tok_00000001⟧")
    assert removed == 3  # a + c + d
    assert store.get("⟦tok_00000001⟧") is None
    assert store.get("⟦tok_00000003⟧") is None
    assert store.get("⟦tok_00000004⟧") is None
    assert store.get("⟦tok_00000002⟧") is not None
    assert store.get("⟦tok_00000005⟧") is not None


def test_invalidate_cascade_unknown_token_returns_zero():
    store = MemoryTokenStore()
    assert store.invalidate_cascade("⟦tok_deadbeef⟧") == 0


# --- expired values stop existing, not just resolving ----------------------
#
# `get` hiding an expired record is not the same as the record being gone: a
# TTL that only governs resolvability leaves the cleartext value in memory for
# the life of the process. `put` sweeps on an interval so nothing has to
# remember to call purge_expired().


def test_expired_records_are_swept_without_an_explicit_call():
    with freeze_time(NOW) as frozen:
        store = MemoryTokenStore()
        store.purge_interval_s = 30
        store.put(_rec("⟦tok_00000001⟧", ttl_min=1))

        frozen.tick(delta=timedelta(minutes=2))
        assert store.get("⟦tok_00000001⟧") is None  # hidden...
        assert "⟦tok_00000001⟧" in store._records  # ...but still resident

        store.put(_rec("⟦tok_00000002⟧"))
        assert "⟦tok_00000001⟧" not in store._records


def test_sweep_keeps_live_records():
    with freeze_time(NOW) as frozen:
        store = MemoryTokenStore()
        store.purge_interval_s = 30
        store.put(_rec("⟦tok_00000001⟧", ttl_min=1))
        store.put(_rec("⟦tok_00000002⟧", ttl_min=600))

        frozen.tick(delta=timedelta(minutes=2))
        store.put(_rec("⟦tok_00000003⟧"))

        assert "⟦tok_00000001⟧" not in store._records
        assert store.get("⟦tok_00000002⟧") is not None
        assert store.get("⟦tok_00000003⟧") is not None


def test_sweep_is_amortized_and_does_not_run_on_every_put():
    # The cost of the scan is bounded by the interval, so an expired record can
    # outlive its TTL by up to that much. Deliberate, and the reason the
    # interval is settable.
    with freeze_time(NOW) as frozen:
        store = MemoryTokenStore()
        store.purge_interval_s = 600
        store.put(_rec("⟦tok_00000001⟧", ttl_min=1))

        frozen.tick(delta=timedelta(minutes=2))
        store.put(_rec("⟦tok_00000002⟧"))

        assert "⟦tok_00000001⟧" in store._records
        assert store.get("⟦tok_00000001⟧") is None  # still never resolves


def test_explicit_purge_still_works_alongside_the_sweep():
    with freeze_time(NOW) as frozen:
        store = MemoryTokenStore()
        store.purge_interval_s = 600
        store.put(_rec("⟦tok_00000001⟧", ttl_min=1))
        frozen.tick(delta=timedelta(minutes=2))
        assert store.purge_expired() == 1
