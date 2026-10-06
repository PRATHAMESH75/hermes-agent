"""Write a settled Bot Chat hand-off's outcome back to its job (#134092).

``_record_delivery_verification`` stores receipts still ``queued``/``claimed`` at admission as
``last_delivery_queued`` and the run finishes ``delivery_queued``. The live owner settles the
receipt seconds later, but nothing told the job, so ``cron list`` showed "delivery still in
progress" until the next run (a week, for a weekly job). The receipt stays the source of truth:
this only copies its terminal status onto the job record, no new state.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_PENDING = ("queued", "claimed")


def _receipt_home(target: str) -> Path:
    """Home whose mailbox holds the receipt, matching ``_deliver_to_bot_chat``'s choice."""
    from hermes_constants import get_hermes_home
    from hermes_cli.profiles import get_profile_dir

    profile = target.partition(":")[2]
    return get_hermes_home() if profile in ("", "(own)") else get_profile_dir(profile)


def _terminal_receipts(queued: Dict[str, Any]) -> Dict[str, tuple]:
    """target -> (delivery_id, receipt), for every entry whose receipt has left queued/claimed."""
    from tools.bot_live_delivery import read_delivery_result

    terminal = {}
    for target, entry in queued.items():
        try:
            record = read_delivery_result(_receipt_home(target), entry["delivery_id"])
        except Exception as exc:  # unreadable/absent profile: leave it queued, never guess
            logger.debug("Bot Chat receipt for %s unreadable: %s", target, exc)
            continue
        if record is not None and record.get("status") not in _PENDING:
            terminal[target] = (entry["delivery_id"], record)
    return terminal


def _settle(job: Dict[str, Any], terminal: Dict[str, tuple]) -> Dict[str, Any]:
    """Field changes that retire *terminal* entries still recorded on *job* (by delivery id)."""
    current = job.get("last_delivery_queued") or {}
    done = {target: record for target, (delivery_id, record) in terminal.items()
            if (current.get(target) or {}).get("delivery_id") == delivery_id}
    if not done:
        return {}
    remaining = {target: entry for target, entry in current.items() if target not in done} or None
    changes: Dict[str, Any] = {"last_delivery_queued": remaining}
    failures = [f"{target} {record['status']}: {record.get('error') or record.get('reason') or 'no details'}"
                for target, record in done.items() if record["status"] != "settled"]
    if failures:
        changes["last_delivery_error"] = "; ".join(failures)
        if job.get("last_status") == "delivery_queued":
            changes["last_status"] = "delivery_failed"
    elif remaining is None and job.get("last_status") == "delivery_queued":
        changes["last_status"] = "ok"
    return changes


def reconcile_queued_deliveries(jobs: List[Dict[str, Any]]) -> None:
    """Retire settled Bot Chat receipts from *jobs* in place and persist the change.

    Compare-and-set under the jobs lock on the delivery id, so a newer run's hand-off that
    replaced the entry meanwhile is never cleared by an older receipt.
    """
    from cron.jobs import _with_job, save_jobs

    for job in jobs:
        queued = job.get("last_delivery_queued")
        if not isinstance(queued, dict) or not queued:
            continue
        terminal = _terminal_receipts(queued)
        if not terminal:
            continue

        def apply(stored_jobs, _i, stored, terminal=terminal):
            changes = _settle(stored, terminal)
            if changes:
                stored.update(changes)
                save_jobs(stored_jobs)
            return changes

        try:
            job.update(_with_job(job["id"], apply, missing={}))
        except Exception as exc:  # bookkeeping must never fail a tick or a listing
            logger.debug("Job '%s': could not settle queued Bot Chat delivery: %s", job.get("id"), exc)
