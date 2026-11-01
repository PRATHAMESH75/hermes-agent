"""While the word index is down, search answers from canonical rows and must keep its whole-token meaning."""

import sqlite3

import pytest

from hermes_state import SessionDB


@pytest.fixture
def stale_db(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("s1", source="test")
    for text in ("TKtx12 turn", "TKtx128 turn", "TKtx129 turn", "snake_case note"):
        db.append_message("s1", "user", text)
    raw = sqlite3.connect(str(tmp_path / "state.db"))
    raw.execute("UPDATE messages_fts_data SET block = X'DEADBEEFDEADBEEFDEADBEEFDEADBEEF'")
    raw.commit()
    raw.close()
    db.append_message("s1", "user", "canonical survives")  # the failed trigger write trips fail-open
    assert db._fts_stale
    yield db
    db.close()


def _snippets(db, query, **kwargs):
    return [row["snippet"] for row in db.search_messages(query, **kwargs)]


def test_term_does_not_match_a_longer_token_on_any_page(stale_db):
    assert _snippets(stale_db, "TKtx12") == ["TKtx12 turn"]
    assert _snippets(stale_db, "TKtx12", limit=1) == ["TKtx12 turn"]
    assert _snippets(stale_db, "TKtx12", limit=1, offset=1) == []


def test_not_only_excludes_whole_tokens(stale_db):
    assert _snippets(stale_db, "turn NOT TKtx12") == ["TKtx129 turn", "TKtx128 turn"]


def test_prefix_star_still_reaches_longer_tokens(stale_db):
    assert _snippets(stale_db, "TKtx12*", sort="oldest") == ["TKtx12 turn", "TKtx128 turn", "TKtx129 turn"]


def test_underscore_separates_tokens_like_the_index(stale_db):
    assert _snippets(stale_db, "snake") == ["snake_case note"]


def test_scan_pages_past_a_full_batch_of_near_misses(stale_db, monkeypatch):
    monkeypatch.setattr("hermes_state_search._LIKE_TOKEN_SCAN_BATCH", 2)
    assert _snippets(stale_db, "TKtx12", sort="newest") == ["TKtx12 turn"]


def test_or_group_and_case_folding(stale_db):
    assert _snippets(stale_db, "tktx128 OR snake", sort="oldest") == ["TKtx128 turn", "snake_case note"]


def test_explicit_tool_role_search_stays_a_substring_scan(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("s1", source="test")
    db.append_message("s1", "tool", "error code TKtx128", tool_name="terminal", tool_call_id="c1")
    assert len(db.search_messages("TKtx12", role_filter=["tool"])) == 1
    db.close()
