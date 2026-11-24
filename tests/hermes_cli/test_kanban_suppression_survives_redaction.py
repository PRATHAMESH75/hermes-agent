"""The "dispatcher stuck" warning keeps its held-back counts through log redaction (#159275).

``describe_suppression`` used to render ``blocker_auth=1``; the log redactor reads any
``<word>_auth=<value>`` as a credential assignment, so the gateway log showed
``blocker_auth=***`` and the count was lost.
"""
import logging

from agent.redact import RedactingFormatter
from hermes_cli.kanban_db_dispatch import DispatchResult, describe_suppression

_REASONS = ("active_pr", "blocker_auth", "infrastructure_cooldown", "rate_limit_cooldown", "recent_success")


def _logged(message: str) -> str:
    record = logging.LogRecord("gateway.kanban_watchers", logging.WARNING, __file__, 1, message, None, None)
    return RedactingFormatter("%(message)s").format(record)


def test_every_held_back_count_survives_the_log_redactor():
    res = DispatchResult(
        respawn_guarded=[(f"t{i}", reason) for i, reason in enumerate(_REASONS)] + [("t9", "blocker_auth")],
        rate_limited=["t7"], skipped_locked=True, memory_pressure="critical",
    )
    held = describe_suppression([res])

    logged = _logged(f"kanban dispatcher stuck: 0 workers spawned. Last tick held back: {held}.")

    assert "***" not in logged
    for part in ("blocker_auth: 2", "active_pr: 1", "rate_limited: 1", "skipped_locked: 1",
                 "memory_pressure: critical"):
        assert part in logged


def test_nothing_held_back_is_still_empty():
    assert describe_suppression([None, DispatchResult()]) == ""
