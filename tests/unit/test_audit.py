"""The audit command — the only way to tell a working install from no install.

In Mode C the display hook puts real values back before anyone reads them, so
the screen looks identical whether Blindfold ran or not. These tests are about
the one thing that can tell the difference: the transcript, cross-referenced
against the vault.
"""

import json
from datetime import datetime, timedelta, timezone

from blindfold.audit import MIN_INTERESTING, audit, read_transcript, session_ids_in
from blindfold.core.lineage import Lineage, Policy, VaultRecord
from blindfold.core.vault import MemoryTokenStore
from blindfold.ports.token_store import TokenStore

SESSION = "S1"


def _store_with(*values, session: str = SESSION, semantic: str | None = "salary"):
    store = MemoryTokenStore()
    tokens = []
    for value in values:
        token = TokenStore.mint_token()
        store.put(
            VaultRecord(
                token=token,
                value=value,
                dtype="object",
                semantic_type=semantic,
                unit=None,
                session_id=session,
                created_at=datetime.now(tz=timezone.utc),
                ttl=datetime.now(tz=timezone.utc) + timedelta(hours=1),
                lineage=Lineage(op="tool_result"),
                policy=Policy(),
            )
        )
        tokens.append(token)
    return store, tokens


def test_a_transcript_holding_only_placeholders_is_clean():
    store, [token] = _store_with(71000)
    report = audit(f"Andrea earns {token}.", store, SESSION)

    assert report.clean
    assert report.records == 1
    assert report.placeholders_seen == 1
    assert "No hidden value appears" in report.render()


def test_a_value_in_the_transcript_is_reported_with_its_token():
    store, [token] = _store_with(71000)
    report = audit('the tool returned {"salary": 71000}', store, SESSION)

    assert not report.clean
    assert report.leaks[0].token == token
    assert report.leaks[0].value == "71000"
    assert report.leaks[0].semantic_type == "salary"
    assert "LEAKED" in report.render()


def test_every_cell_of_a_hidden_table_is_checked():
    # A collective token hides a list; a leak can be any one cell of it.
    rows = [{"name": "Andrea Tuscano", "salary": 71000}, {"name": "Maria Rossi", "salary": 55000}]
    store, _ = _store_with(rows)
    assert not audit("...Maria Rossi...", store, SESSION).clean
    assert not audit("...55000...", store, SESSION).clean
    assert audit("nothing to see", store, SESSION).clean


def test_values_too_short_to_mean_anything_are_skipped():
    # "Eng" appears in any transcript for reasons unrelated to a leak, and
    # reporting it would bury a real finding.
    store, _ = _store_with("Eng")
    report = audit("the Engineering department", store, SESSION)
    assert report.clean
    assert report.skipped_as_too_short == 1
    assert str(MIN_INTERESTING) in report.render()


def test_another_session_is_not_audited():
    store, [token] = _store_with(71000, session="somebody_else")
    report = audit(f"71000 and {token}", store, SESSION)
    assert report.records == 0
    assert report.clean, "a record from another session is not this session's leak"


def test_placeholders_that_do_not_resolve_are_counted():
    store, [token] = _store_with(71000)
    report = audit(f"{token} and ⟦tok_deadbeefdeadbeef⟧", store, SESSION)
    assert len(report.unresolved) == 1
    assert "do not resolve" in report.render()


def test_an_empty_vault_says_so_rather_than_claiming_success():
    # "no leaks" is also what a config that declares nothing produces.
    store = MemoryTokenStore()
    report = audit("anything at all", store, SESSION)
    assert report.clean
    assert "protects nothing" in report.render()


def test_a_vault_with_records_but_no_placeholders_says_so():
    store, _ = _store_with("Andrea Tuscano")
    report = audit("an unrelated conversation", store, SESSION)
    assert "wrong transcript" in report.render()


# --- blind_compute results that coincide with public text -----------------


def _store_with_compute_result(value, *, session: str = SESSION):
    """A record the way blindfold_compute actually mints it: op=blind_compute."""
    store = MemoryTokenStore()
    token = TokenStore.mint_token()
    store.put(
        VaultRecord(
            token=token,
            value=value,
            dtype="string" if isinstance(value, str) else "number",
            semantic_type=None,
            unit=None,
            session_id=session,
            created_at=datetime.now(tz=timezone.utc),
            ttl=datetime.now(tz=timezone.utc) + timedelta(hours=1),
            lineage=Lineage(op="blind_compute", inputs=("tok_a", "tok_b")),
            policy=Policy(),
        )
    )
    return store, token


def test_a_literal_the_model_wrote_into_compute_code_is_not_a_leak():
    # The model already knew the name — it typed it itself, choosing between
    # two known names based on a hidden salary comparison. The token wraps
    # the choice, not the name.
    store, token = _store_with_compute_result("Andrea Tuscano")
    transcript = (
        '{"type":"tool_use","name":"blindfold_compute","input":'
        '{"code":"result = \'Andrea Tuscano\' if resolve(\'tok_a\') > resolve(\'tok_b\') '
        'else \'Manuel Pernigotto\'","inputs":["tok_a","tok_b"]}}'
        f' ...later the screen shows {token} resolved as Andrea Tuscano...'
    )
    report = audit(transcript, store, SESSION)
    assert report.clean
    assert len(report.explained) == 1
    assert report.explained[0].value == "Andrea Tuscano"
    assert "matched but explained" in report.render()


def test_a_computed_number_never_typed_as_a_literal_is_still_a_leak():
    # Contrast case: a sum pulled purely out of resolve(...) calls. It was
    # never a literal in the code, so the carve-out must not swallow it.
    store, token = _store_with_compute_result(180000)
    transcript = (
        '{"type":"tool_use","name":"blindfold_compute","input":'
        '{"code":"result = resolve(\'tok_a\') + resolve(\'tok_b\')","inputs":["tok_a","tok_b"]}}'
        ' the total came out to 180000 apparently'
    )
    report = audit(transcript, store, SESSION)
    assert not report.clean
    assert report.leaks[0].value == "180000"
    assert not report.explained


# --- reading a Claude Code transcript --------------------------------------


def _write_jsonl(tmp_path, rows):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return p


def test_the_session_is_taken_from_the_transcript(tmp_path):
    p = _write_jsonl(tmp_path, [{"sessionId": "abc"}, {"sessionId": "abc"}, {"sessionId": "def"}])
    assert session_ids_in(p) == ["abc", "def"]


def test_reading_normalises_escaped_placeholders(tmp_path):
    # Written escaped by one writer and literally by another, a placeholder has
    # to be found either way.
    token = TokenStore.mint_token()
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"sessionId": "S1", "text": token}, ensure_ascii=True), encoding="utf-8")
    assert token in read_transcript(p)


def test_a_plain_text_log_is_read_as_is(tmp_path):
    p = tmp_path / "session.log"
    p.write_text("salary was 71000", encoding="utf-8")
    assert read_transcript(p) == "salary was 71000"


def test_a_malformed_line_does_not_stop_the_read(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"sessionId": "S1"}\nnot json at all\n', encoding="utf-8")
    assert "not json at all" in read_transcript(p)
    assert session_ids_in(p) == ["S1"]
