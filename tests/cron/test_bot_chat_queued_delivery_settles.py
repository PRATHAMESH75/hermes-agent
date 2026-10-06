"""#134092: a Bot Chat hand-off the owner settles after the run must not leave the job
reading ``delivery_queued`` until its next run. Real job store + real receipt mailbox."""

import uuid

from cron.delivery_reconcile import reconcile_queued_deliveries
from cron.jobs import create_job, get_job, list_jobs, update_job
from hermes_constants import get_hermes_home
from tools.bot_live_delivery import claim_pending_delivery, complete_delivery, deliver_to_live_owner


def _queued_job_with_claimed_receipt():
    home = get_hermes_home().resolve()
    owner = {"profile_home": str(home), "session_id": "s1", "lease_id": "l1", "live_session_id": "ls1"}
    key = uuid.uuid4().hex
    deliver_to_live_owner(home, owner, "cron output", delivery_id=key)
    claim_pending_delivery(home, owner)
    job = create_job(prompt="x", schedule="every 1h", deliver="bot-chat")
    update_job(job["id"], {"last_status": "delivery_queued",
                           "last_delivery_queued": {"bot-chat:(own)": {"status": "queued", "delivery_id": key}}})
    return job["id"], home, key


def test_settled_receipt_completes_the_job_record():
    job_id, home, key = _queued_job_with_claimed_receipt()
    reconcile_queued_deliveries(list_jobs(include_disabled=True))
    assert get_job(job_id)["last_status"] == "delivery_queued"  # still claimed: stays in progress

    complete_delivery(home, key, status="settled", reply="done")
    jobs = list_jobs(include_disabled=True)
    reconcile_queued_deliveries(jobs)

    stored = get_job(job_id)
    assert (stored["last_status"], stored.get("last_delivery_queued")) == ("ok", None)
    assert [j for j in jobs if j["id"] == job_id][0]["last_status"] == "ok"  # caller's copy too


def test_failed_receipt_records_a_delivery_failure_and_newer_handoff_survives():
    job_id, home, key = _queued_job_with_claimed_receipt()
    complete_delivery(home, key, status="failed", error="owner turn crashed")
    stale_view = list_jobs(include_disabled=True)
    # A newer run replaced the hand-off before reconcile ran: an old receipt must not clear it.
    newer = {"bot-chat:(own)": {"status": "queued", "delivery_id": uuid.uuid4().hex}}
    update_job(job_id, {"last_delivery_queued": newer})
    reconcile_queued_deliveries(stale_view)
    assert get_job(job_id)["last_delivery_queued"] == newer

    update_job(job_id, {"last_delivery_queued": {"bot-chat:(own)": {"status": "queued", "delivery_id": key}}})
    reconcile_queued_deliveries(list_jobs(include_disabled=True))
    stored = get_job(job_id)
    assert stored["last_status"] == "delivery_failed"
    assert stored.get("last_delivery_queued") is None
    assert "owner turn crashed" in stored["last_delivery_error"]
