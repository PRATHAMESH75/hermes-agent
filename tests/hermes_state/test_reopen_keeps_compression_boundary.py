"""A reopen must not erase the compression stamp a published continuation hangs off (#148047)."""

from __future__ import annotations

import pytest

from hermes_state import SessionDB
from hermes_state_compression import _CHAIN_STEP_SQL
from hermes_state_keys import local_identity
from hermes_state_local_lineage import validate_local_lineage


@pytest.fixture()
def db(tmp_path):
    session_db = SessionDB(db_path=tmp_path / "state.db")
    try:
        yield session_db
    finally:
        session_db.close()


def _publish_child(db: SessionDB, parent: str = "parent", child: str = "child") -> None:
    db.create_session(parent, source="cli", model="test-model")
    db.publish_compression_child(
        parent_session_id=parent, child_session_id=child, source="cli", messages=[{"role": "user", "content": "handoff"}],
        model="test-model", require_compression_lease=False,
    )


def test_reopen_of_compressed_parent_keeps_the_lineage_valid(db: SessionDB) -> None:
    root = local_identity("default", "p1", "r1")
    _publish_child(db, parent=root)
    stamped = db.get_session(root)

    db.reopen_session(root)

    parent = db.get_session(root)
    assert parent["end_reason"] == "compression"
    assert parent["ended_at"] == stamped["ended_at"]
    with db._read_ctx() as conn:
        assert conn.execute(_CHAIN_STEP_SQL, (root,)).fetchone()["id"] == "child"
        assert validate_local_lineage(conn, {
            "session_id": root, "profile_id": "default", "principal_id": "p1", "request_id": "r1",
            "lineage": [root, "child"], "entry": {"session_id": "child"}, "policy": {},
        }) == "child"


def test_reopen_clears_compression_stamp_with_no_continuation(db: SessionDB) -> None:
    db.create_session("orphan", source="cli")
    db.end_session("orphan", "compression")
    db.create_session("branch", source="cli", parent_session_id="orphan",
                      model_config={"_branched_from": "orphan"})

    db.reopen_session("orphan")

    row = db.get_session("orphan")
    assert row["ended_at"] is None and row["end_reason"] is None


def test_reopen_clears_other_end_reasons_and_adoption_can_restamp(db: SessionDB) -> None:
    db.create_session("closed", source="cli")
    db.end_session("closed", "tui_close")
    db.reopen_session("closed")
    assert db.get_session("closed")["end_reason"] is None

    _publish_child(db)
    db.reopen_session("parent", drop_compression_boundary=True)
    db.end_session("parent", "adopted_by_profile")
    assert db.get_session("parent")["end_reason"] == "adopted_by_profile"
