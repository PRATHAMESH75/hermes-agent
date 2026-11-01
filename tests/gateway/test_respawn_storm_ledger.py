"""The respawn-storm breaker keeps deciding when its start ledger cannot be written (#147538)."""

import logging
import time

import pytest

from gateway import status


@pytest.mark.platforms("linux")
@pytest.mark.parametrize(
    "prior_starts,expected_count",
    [(5, 6), (1, None)],
    ids=["over-threshold-still-trips", "under-threshold-stays-quiet"],
)
def test_unwritable_ledger_keeps_the_breaker_deciding(tmp_path, monkeypatch, caplog, prior_starts, expected_count):
    # A full disk or read-only home is when respawns are likeliest: the starts the ledger
    # already holds must still count, and the operator must see why it stopped growing.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ledger = tmp_path / "gateway-starts.log"
    ledger.write_text(f"{time.time()!r}\n" * prior_starts, encoding="utf-8")
    before = ledger.read_bytes()
    tmp_path.chmod(0o500)
    try:
        with caplog.at_level(logging.WARNING, logger="gateway.status"):
            result = status.record_start_and_check_storm(max_starts=5, window_s=120.0)
    finally:
        tmp_path.chmod(0o700)
    assert (result.count if result else None) == expected_count
    assert ledger.read_bytes() == before
    assert "start ledger" in caplog.text
