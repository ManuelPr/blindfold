"""One behavioural suite both stores must satisfy.

A second implementation of a port is only safe if it agrees with the first
everywhere the rest of the system relies on. These tests use the public
interface only, so they say nothing about how either store keeps its records —
which is the point.
"""

from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from blindfold.core.lineage import Lineage, Policy, VaultRecord
from blindfold.core.sqlite_store import SQLiteTokenStore
from blindfold.core.vault import MemoryTokenStore
from blindfold.ports.token_store import TokenStore

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def _rec(
    token: str,
    *,
    value="val",
    dtype="string",
    ttl_min: int = 60,
    session: str = "s",
    inputs: tuple[str, ...] = (),
    op: str = "literal",
) -> VaultRecord:
    return VaultRecord(
        token=token,
        value=value,
        dtype=dtype,
        semantic_type="salary",
        unit="EUR/year",
        session_id=session,
        created_at=NOW,
        ttl=NOW + timedelta(minutes=ttl_min),
        lineage=Lineage(op=op, inputs=inputs, code_digest="sha256:abc", tool="hr.get", path="$.salary"),
        policy=Policy(),
    )


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        made = MemoryTokenStore()
    else:
        made = SQLiteTokenStore(tmp_path / "vault.db")
    yield made
    close = getattr(made, "close", None)
    if close is not None:
        close()


@freeze_time(NOW)
def test_put_then_get_returns_an_equal_record(store):
    r = _rec("⟦tok_00000001⟧")
    store.put(r)
    assert store.get("⟦tok_00000001⟧") == r


@freeze_time(NOW)
@pytest.mark.parametrize(
    "value, dtype",
    [
        (62000, "number"),
        (0.1 + 0.2, "number"),
        ("Andrea Tuscano", "string"),
        (True, "boolean"),
        (None, "object"),
        ({"street": "1 rd", "city": "X"}, "object"),
        ([1, "two", {"three": 3}], "object"),
        ("unicode ⟦👋⟧ and 'quotes'", "string"),
    ],
)
def test_values_survive_a_round_trip_unchanged(store, value, dtype):
    store.put(_rec("⟦tok_00000001⟧", value=value, dtype=dtype))
    got = store.resolve("⟦tok_00000001⟧")
    assert got == value
    assert type(got) is type(value)


def test_get_and_resolve_unknown_return_none(store):
    assert store.get("⟦tok_deadbeef⟧") is None
    assert store.resolve("⟦tok_deadbeef⟧") is None


def test_expired_records_are_hidden_from_get(store):
    with freeze_time(NOW) as frozen:
        store.put(_rec("⟦tok_00000001⟧", ttl_min=1))
        assert store.get("⟦tok_00000001⟧") is not None
        frozen.tick(delta=timedelta(minutes=2))
        assert store.get("⟦tok_00000001⟧") is None
        assert store.resolve("⟦tok_00000001⟧") is None


def test_find_by_session_isolates_and_hides_expired(store):
    with freeze_time(NOW) as frozen:
        store.put(_rec("⟦tok_00000001⟧", session="a"))
        store.put(_rec("⟦tok_00000002⟧", session="b"))
        store.put(_rec("⟦tok_00000003⟧", session="a", ttl_min=1))

        assert {r.token for r in store.find_by_session("a")} == {
            "⟦tok_00000001⟧",
            "⟦tok_00000003⟧",
        }
        assert {r.token for r in store.find_by_session("b")} == {"⟦tok_00000002⟧"}

        frozen.tick(delta=timedelta(minutes=2))
        assert {r.token for r in store.find_by_session("a")} == {"⟦tok_00000001⟧"}


@freeze_time(NOW)
def test_put_same_token_twice_replaces(store):
    store.put(_rec("⟦tok_00000001⟧", value="first"))
    store.put(_rec("⟦tok_00000001⟧", value="second"))
    assert store.resolve("⟦tok_00000001⟧") == "second"
    assert len(store.find_by_session("s")) == 1


@freeze_time(NOW)
def test_purge_expired_returns_how_many_it_removed(store):
    with freeze_time(NOW) as frozen:
        store.put(_rec("⟦tok_00000001⟧", ttl_min=1))
        store.put(_rec("⟦tok_00000002⟧", ttl_min=600))
        frozen.tick(delta=timedelta(minutes=2))
        assert store.purge_expired() == 1
        assert store.get("⟦tok_00000002⟧") is not None


@freeze_time(NOW)
def test_invalidate_cascade_removes_descendants_only(store):
    store.put(_rec("⟦tok_00000001⟧"))
    store.put(_rec("⟦tok_00000002⟧"))
    store.put(_rec("⟦tok_00000003⟧", inputs=("⟦tok_00000001⟧",), op="blind_compute"))
    store.put(_rec("⟦tok_00000004⟧", inputs=("⟦tok_00000003⟧",), op="blind_compute"))
    store.put(_rec("⟦tok_00000005⟧", inputs=("⟦tok_00000002⟧",), op="blind_compute"))

    assert store.invalidate_cascade("⟦tok_00000001⟧") == 3

    assert store.get("⟦tok_00000001⟧") is None
    assert store.get("⟦tok_00000003⟧") is None
    assert store.get("⟦tok_00000004⟧") is None
    assert store.get("⟦tok_00000002⟧") is not None
    assert store.get("⟦tok_00000005⟧") is not None


def test_invalidate_cascade_on_unknown_token_returns_zero(store):
    assert store.invalidate_cascade("⟦tok_deadbeef⟧") == 0


def test_mint_token_comes_from_the_port(store):
    token = TokenStore.mint_token()
    assert token.startswith("⟦tok_") and token.endswith("⟧")
    assert len(token.removeprefix("⟦tok_").removesuffix("⟧")) == 8


# --- what only the persistent store can do --------------------------------


@freeze_time(NOW)
def test_sqlite_records_outlive_the_process_that_wrote_them(tmp_path):
    path = tmp_path / "vault.db"
    writer = SQLiteTokenStore(path)
    writer.put(_rec("⟦tok_00000001⟧", value=62000, dtype="number"))
    writer.close()

    reader = SQLiteTokenStore(path)
    try:
        assert reader.resolve("⟦tok_00000001⟧") == 62000
    finally:
        reader.close()


@freeze_time(NOW)
def test_sqlite_is_shared_between_two_open_connections(tmp_path):
    # The host-integration case: one process tokenizes, another rehydrates,
    # seconds apart, against the same file.
    path = tmp_path / "vault.db"
    tokenizer = SQLiteTokenStore(path)
    rehydrator = SQLiteTokenStore(path)
    try:
        tokenizer.put(_rec("⟦tok_00000001⟧", value="Andrea Tuscano"))
        assert rehydrator.resolve("⟦tok_00000001⟧") == "Andrea Tuscano"
    finally:
        tokenizer.close()
        rehydrator.close()


@freeze_time(NOW)
def test_sqlite_creates_missing_parent_directories(tmp_path):
    store = SQLiteTokenStore(tmp_path / "nested" / "dir" / "vault.db")
    try:
        assert (tmp_path / "nested" / "dir" / "vault.db").exists()
    finally:
        store.close()


@freeze_time(NOW)
def test_sqlite_survives_concurrent_use_from_threads(tmp_path):
    # The proxy runs blind compute in a worker thread, so the connection is
    # touched from a thread that did not create it.
    import threading

    store = SQLiteTokenStore(tmp_path / "vault.db")
    errors = []

    def hammer(offset: int) -> None:
        try:
            for i in range(25):
                token = f"⟦tok_{offset:04d}{i:04d}⟧"
                store.put(_rec(token, value=offset * 1000 + i))
                assert store.resolve(token) == offset * 1000 + i
        except Exception as exc:  # pragma: no cover - only on regression
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert errors == []
        assert len(store.find_by_session("s")) == 100
    finally:
        store.close()
