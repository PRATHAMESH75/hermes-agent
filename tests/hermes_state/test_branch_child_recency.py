"""Every branch path that copies a transcript through ``create_session`` gets its own recency (#146712)."""
import time

from hermes_state import SessionDB

OLD = 1_700_000_000.0


def _listed(db, session_id):
    return next(s for s in db.list_sessions_rich(limit=50) if s['id'] == session_id)


def test_copied_old_timestamps_do_not_bury_a_new_branch(tmp_path):
    db = SessionDB(tmp_path / 'state.db')
    db.create_session('parent', source='cli')
    forked_after = time.time()
    db.create_session('branch', source='cli', parent_session_id='parent', model_config={'_branched_from': 'parent'})
    db.append_messages_batch('branch', [{'role': 'user', 'content': 'old', 'timestamp': OLD}], chunk_rows=500)
    db.set_session_title('branch', 'parent #2')

    assert _listed(db, 'branch')['last_active'] >= forked_after
    assert db.get_messages('branch')[0]['timestamp'] == OLD


def test_a_row_without_the_branch_marker_keeps_message_recency(tmp_path):
    """Importers and restores write historic transcripts; a plain row must not look freshly active."""
    db = SessionDB(tmp_path / 'state.db')
    db.create_session('imported', source='cli')
    db.append_messages_batch('imported', [{'role': 'user', 'content': 'old', 'timestamp': OLD}], chunk_rows=500)

    assert db.get_session('imported')['last_activity_at'] is None
    assert _listed(db, 'imported')['last_active'] == OLD


def test_re_creating_an_existing_branch_row_does_not_refresh_its_recency(tmp_path):
    db = SessionDB(tmp_path / 'state.db')
    db.create_session('branch', source='cli', model_config={'_branched_from': 'parent'})
    db._execute_write(lambda conn: conn.execute('UPDATE sessions SET last_activity_at=? WHERE id=?', (OLD, 'branch')))

    db.ensure_session('branch', source='cli', model_config={'_branched_from': 'parent'})

    assert db.get_session('branch')['last_activity_at'] == OLD
