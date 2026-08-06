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
    assert len(token.removeprefix("⟦tok_").removesuffix("⟧")) == 16


@freeze_time(NOW)
def test_stores_survive_concurrent_use_from_threads(store):
    # Not a persistence question: the proxy runs blind compute in a worker
    # thread while the other pump keeps tokenizing tool results on the event
    # loop, so *every* store gets written from two threads at once.
    # purge_interval_s=0 makes each put sweep, which is where a store that
    # iterates its own records unsynchronised trips over a concurrent insert.
    import sys
    import threading

    for i in range(3000):
        # A vault with something in it: the sweep's scan has to be long enough
        # for a second thread to get in the middle of it.
        store.put(_rec(f"⟦tok_old{i:05d}⟧", session="old"))
    store.purge_interval_s = 0.0
    # The fixture built the store outside frozen time, so its purge clock sits
    # in the future and every sweep would decline to run.
    store._last_purge = NOW
    errors = []

    def hammer(offset: int) -> None:
        try:
            for i in range(200):
                token = f"⟦tok_{offset:04d}{i:04d}⟧"
                store.put(_rec(token, value=offset * 1000 + i))
                assert store.resolve(token) == offset * 1000 + i
        except Exception as exc:  # pragma: no cover - only on regression
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(4)]
    switch = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # hand the GIL over often, so the race shows
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(switch)
    assert errors == []
    assert len(store.find_by_session("s")) == 800


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


# --- encryption at rest ----------------------------------------------------
#
# The whole question was where the key lives. It comes from outside the file,
# and there is deliberately no way to keep it beside the database.

import base64
import os

from blindfold.core.sqlite_store import VaultKeyError

KEY = base64.b64encode(bytes(range(32))).decode()


@freeze_time(NOW)
def test_encrypted_values_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("BLINDFOLD_VAULT_KEY", KEY)
    store = SQLiteTokenStore(tmp_path / "vault.db", encrypt=True)
    try:
        store.put(_rec("⟦tok_00000001⟧", value={"iban": "IT60X0542811101000000123456"}))
        assert store.resolve("⟦tok_00000001⟧") == {"iban": "IT60X0542811101000000123456"}
    finally:
        store.close()


@freeze_time(NOW)
def test_the_value_is_not_in_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("BLINDFOLD_VAULT_KEY", KEY)
    path = tmp_path / "vault.db"
    store = SQLiteTokenStore(path, encrypt=True)
    try:
        store.put(_rec("⟦tok_00000001⟧", value="IT60X0542811101000000123456"))
    finally:
        store.close()
    assert b"IT60X0542811101000000123456" not in path.read_bytes()


@freeze_time(NOW)
def test_a_cleartext_store_does_leave_the_value_in_the_file(tmp_path):
    # The contrast is the point: without encryption the file is the secret.
    path = tmp_path / "vault.db"
    store = SQLiteTokenStore(path)
    try:
        store.put(_rec("⟦tok_00000001⟧", value="IT60X0542811101000000123456"))
    finally:
        store.close()
    assert b"IT60X0542811101000000123456" in path.read_bytes()


@freeze_time(NOW)
def test_the_wrong_key_does_not_silently_return_garbage(tmp_path, monkeypatch):
    path = tmp_path / "vault.db"
    monkeypatch.setenv("BLINDFOLD_VAULT_KEY", KEY)
    writer = SQLiteTokenStore(path, encrypt=True)
    writer.put(_rec("⟦tok_00000001⟧", value=71000))
    writer.close()

    monkeypatch.setenv("BLINDFOLD_VAULT_KEY", base64.b64encode(bytes(32)).decode())
    reader = SQLiteTokenStore(path, encrypt=True)
    try:
        with pytest.raises(VaultKeyError, match="would not open"):
            reader.resolve("⟦tok_00000001⟧")
    finally:
        reader.close()


@freeze_time(NOW)
def test_a_ciphertext_cannot_be_moved_to_another_row(tmp_path, monkeypatch):
    # The token is the associated data, so a stolen row does not open elsewhere.
    monkeypatch.setenv("BLINDFOLD_VAULT_KEY", KEY)
    path = tmp_path / "vault.db"
    store = SQLiteTokenStore(path, encrypt=True)
    try:
        store.put(_rec("⟦tok_00000001⟧", value="secret"))
        store.put(_rec("⟦tok_00000002⟧", value="other"))
        stolen = store._conn.execute(
            "SELECT value FROM records WHERE token = '⟦tok_00000001⟧'"
        ).fetchone()["value"]
        store._conn.execute(
            "UPDATE records SET value = ? WHERE token = '⟦tok_00000002⟧'", (stolen,)
        )
        with pytest.raises(VaultKeyError):
            store.resolve("⟦tok_00000002⟧")
    finally:
        store.close()


def test_opening_a_cleartext_file_with_encryption_on_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "vault.db"
    SQLiteTokenStore(path).close()
    monkeypatch.setenv("BLINDFOLD_VAULT_KEY", KEY)
    with pytest.raises(VaultKeyError, match="written cleartext"):
        SQLiteTokenStore(path, encrypt=True)


def test_opening_an_encrypted_file_without_the_key_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "vault.db"
    monkeypatch.setenv("BLINDFOLD_VAULT_KEY", KEY)
    SQLiteTokenStore(path, encrypt=True).close()
    with pytest.raises(VaultKeyError, match="written encrypted"):
        SQLiteTokenStore(path)


def test_a_missing_key_says_how_to_make_one(tmp_path, monkeypatch):
    monkeypatch.delenv("BLINDFOLD_VAULT_KEY", raising=False)
    with pytest.raises(VaultKeyError) as ei:
        SQLiteTokenStore(tmp_path / "vault.db", encrypt=True)
    assert "base64" in str(ei.value)
    assert "next to the vault" in str(ei.value)


def test_a_key_of_the_wrong_length_is_refused(tmp_path):
    with pytest.raises(VaultKeyError, match="32 bytes"):
        SQLiteTokenStore(tmp_path / "vault.db", encrypt=True, key=b"too short")
