"""A new branch is recent even when the conversation it copies is old (#146712).

Recency is the freshest of ``last_activity_at`` and the latest message timestamp, and a branch's
copied rows keep the parent's timestamps, so an unstamped child sorted among the parent's history.
"""
import time
from types import SimpleNamespace

import pytest

from gateway.session_authority import SessionAuthority
from gateway.session_controls import AuthorityConnection
from gateway.session_local import create_local_session
import hermes_state_runtime as rt

OLD = 1_700_000_000.0


def _age_transcript(db, session_id):
    db._execute_write(lambda conn: conn.execute(
        'UPDATE messages SET timestamp=? WHERE session_id=?', (OLD, session_id)))
    db._execute_write(lambda conn: conn.execute(
        'UPDATE sessions SET started_at=?, last_activity_at=? WHERE id=?', (OLD, OLD, session_id)))


def _listed(db, session_id):
    return next(s for s in db.list_sessions_rich(limit=50) if s['id'] == session_id)


@pytest.mark.asyncio
async def test_canonical_branch_of_an_old_pinned_session_is_recent_and_keeps_its_history(tmp_path, monkeypatch):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore

    monkeypatch.setattr(run, '_load_gateway_config', dict)
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    db = store._db
    runner = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False,
                             _evict_cached_agent=lambda route: None, _cached_agent_for=lambda route: None,
                             _adapter_for_source=lambda source: None)
    authority = SessionAuthority(runner, profile_id='owned', instance_id='owner', db=db,
                                 epoch=rt.begin_runtime_epoch(db, instance_id='owner'))
    owner = AuthorityConnection(authority, object(), {'user_id': 'human'})
    ref = create_local_session(authority, owner.actor, dict(request_id='branch-recency', source='cli',
                                                            cwd=str(tmp_path), model='m', toolsets=[]))
    sid = ref.session_id
    db.append_message(sid, 'user', 'old question')
    db.append_message(sid, 'assistant', 'old answer')
    db.set_session_pinned(sid, True)
    _age_transcript(db, sid)
    session = db.get_session(sid)
    forked_after = time.time()
    try:
        reply = await owner.dispatch({'id': 1, 'method': 'session.mutate', 'params': {
            'session_id': sid, 'request_id': 'fork', 'expected_revision': session['runtime_revision'],
            'expected_generation': session['runtime_generation'], 'operation': 'branch', 'payload': {}}})
        child = reply['result']['branched_session_id']

        branch = _listed(db, child)
        assert branch['started_at'] >= forked_after
        assert branch['last_active'] >= forked_after
        assert not branch.get('pinned')
        assert branch['parent_session_id'] == sid
        # The history keeps its own times, and the parent's recency does not move.
        assert {m['timestamp'] for m in db.get_messages(child)} == {OLD}
        assert _listed(db, sid)['last_active'] == OLD
    finally:
        await owner.close()
