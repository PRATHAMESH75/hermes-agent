"""A worker that spawned keeps its claim when the dispatcher's follow-up bookkeeping fails (#147535)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _boom(*_args, **_kwargs):
    raise sqlite3.OperationalError("disk I/O error")


@pytest.mark.parametrize(
    "owner, name",
    [(kbd, "_set_worker_pid"), (kb, "_fire_worker_spawned_hook")],
)
def test_bookkeeping_failure_after_spawn_keeps_the_claim(kanban_home, all_assignees_spawnable, monkeypatch, owner, name):
    spawns = []

    def spawn(task, workspace, board=None):
        spawns.append(task.id)
        return os.getpid()

    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="spawned", assignee="a")
        monkeypatch.setattr(owner, name, _boom)

        res = kbd.dispatch_once(conn, spawn_fn=spawn, failure_limit=2)
        assert [s[0] for s in res.spawned] == [tid]
        assert res.auto_blocked == []

        row = conn.execute(
            "SELECT status, claim_lock, consecutive_failures FROM tasks WHERE id = ?", (tid,),
        ).fetchone()
        assert row["status"] == "running"
        assert row["claim_lock"]
        assert row["consecutive_failures"] == 0
        assert conn.execute(
            "SELECT outcome FROM task_runs WHERE task_id = ?", (tid,),
        ).fetchone()["outcome"] is None

        # No second worker beside the first on the next tick.
        kbd.dispatch_once(conn, spawn_fn=spawn, failure_limit=2)
        assert spawns == [tid]


def test_worker_publishes_the_pid_the_dispatcher_failed_to_record(kanban_home, all_assignees_spawnable, monkeypatch):
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="spawned", assignee="a")
        with monkeypatch.context() as patch:
            patch.setattr(kbd, "_set_worker_pid", _boom)
            kbd.dispatch_once(conn, spawn_fn=lambda *a, **k: os.getpid())
        run_id = kb.get_task(conn, tid).current_run_id
        assert conn.execute("SELECT worker_pid FROM tasks WHERE id = ?", (tid,)).fetchone()[0] is None

        assert kbd.adopt_worker_pid(conn, tid, run_id, os.getpid()) is True
        assert conn.execute("SELECT worker_pid FROM tasks WHERE id = ?", (tid,)).fetchone()[0] == os.getpid()


def test_a_spawn_that_raises_still_books_spawn_failed(kanban_home, all_assignees_spawnable):
    def spawn(task, workspace, board=None):
        raise RuntimeError("profile launcher exploded")

    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="broken", assignee="a")
        res = kbd.dispatch_once(conn, spawn_fn=spawn, failure_limit=5)
        assert res.spawned == []
        row = conn.execute(
            "SELECT status, claim_lock, consecutive_failures FROM tasks WHERE id = ?", (tid,),
        ).fetchone()
        assert (row["status"], row["claim_lock"], row["consecutive_failures"]) == ("ready", None, 1)
