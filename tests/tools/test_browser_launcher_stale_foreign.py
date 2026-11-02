"""A launch record from another PID namespace proves a browser ran there once, not that one runs now.

Recreating a container leaves the old ``SingletonLock``, ``DevToolsActivePort`` and launch record on the volume. The
next launch takes over when the browser is proven gone: its liveness ``flock`` is free under this kernel, or (a record
with no usable proof) a pinned hostname shows nothing here on the profile and nothing on its endpoint. A browser whose
lock is still held, or that cannot be proven gone, is refused as before.
"""

from __future__ import annotations

import json
import os
import signal
import socket

import pytest

from tests.tools.browser_launcher_fakes import kill_fake, pids, write_fake_chromium
from tools import browser_launcher as launcher
from tools import browser_launcher_holders as holders

pytestmark = pytest.mark.platforms("linux")

_DEAD_PID = 3037
_OTHER_BOOT_NS = "4026531836:00000000-0000-0000-0000-000000000000"


def _other_namespace_this_boot() -> str:
    return f"4026999999:{holders.ns_identity().rpartition(':')[2]}"


def _endpoint_nobody_answers() -> str:
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    return f"{port}\n/devtools/browser/gone\n"


def _left_behind(udd, *, host: str, ns: str, liveness: bool):
    """What a recreated container finds on its volume; returns the launch record's path."""
    udd.mkdir()
    os.symlink(f"{host}-{_DEAD_PID}", udd / "SingletonLock")
    os.symlink("old-cookie", udd / "SingletonCookie")
    port_file = _endpoint_nobody_answers()
    (udd / "DevToolsActivePort").write_text(port_file, encoding="utf-8")
    state = launcher._shared_state_dir(str(udd))
    record = state / f"{launcher._profile_digest(str(udd))[:16]}-{_DEAD_PID}-nffffffffffff.json"
    record.write_text(json.dumps({
        "owner_pid": 1, "owner_start_time": 1, "pid": _DEAD_PID, "start_time": 1, "user_data_dir": str(udd),
        "home_key": "elsewhere", "ephemeral": False, "ns": ns, "devtools": port_file.split()[1]}), encoding="utf-8")
    if liveness:
        record.with_suffix(".alive").touch()
    return record


def _launch(script, udd):
    return launcher.launch_chromium(f"profile:{udd}", executable=str(script), user_data_dir=str(udd),
                                    ephemeral=False, headed=False, env=dict(os.environ), timeout=30)


@pytest.fixture
def fake(tmp_path):
    script = write_fake_chromium(tmp_path / "chromium")
    udd = tmp_path / "pinned-profile"
    yield script, udd
    launcher.stop_browser(f"profile:{udd}")
    for spawned in pids(udd, "spawns"):
        kill_fake(spawned, script)


@pytest.mark.live_system_guard_bypass  # the fake browser is ours to stop if a leg failed
@pytest.mark.parametrize("left_behind", [
    pytest.param(lambda: dict(host=socket.gethostname(), ns=_other_namespace_this_boot(), liveness=True),
                 id="free-lock-pinned-hostname"),
    pytest.param(lambda: dict(host="old-container-id", ns=_other_namespace_this_boot(), liveness=True),
                 id="free-lock-new-hostname"),
    pytest.param(lambda: dict(host=socket.gethostname(), ns=_OTHER_BOOT_NS, liveness=False),
                 id="older-hermes-record-pinned-hostname"),
    pytest.param(lambda: dict(host=socket.gethostname(), ns=_OTHER_BOOT_NS, liveness=True),
                 id="free-lock-of-another-boot-pinned-hostname"),
])
def test_a_browser_proven_gone_no_longer_refuses_the_profile(fake, left_behind):
    script, udd = fake
    record = _left_behind(udd, **left_behind())

    browser, err = _launch(script, udd)

    assert err is None and browser is not None and browser.owned
    assert pids(udd, "launches") == [browser.pid]
    assert not record.exists() and not record.with_suffix(".alive").exists()
    assert not (udd / "SingletonCookie").is_symlink()
    assert os.readlink(udd / "SingletonLock").endswith(f"-{browser.pid}")


def test_a_held_liveness_lock_is_a_live_browser_elsewhere_and_is_refused(fake):
    script, udd = fake
    record = _left_behind(udd, host=socket.gethostname(), ns=_other_namespace_this_boot(), liveness=True)
    import fcntl  # windows-footgun: ok — a linux-only module (pytestmark)
    held = os.open(record.with_suffix(".alive"), os.O_RDONLY)
    fcntl.flock(held, fcntl.LOCK_SH)
    try:
        browser, err = _launch(script, udd)
    finally:
        os.close(held)

    assert browser is None and "another environment" in err
    assert pids(udd, "spawns") == []
    assert record.exists() and os.readlink(udd / "SingletonLock").endswith(f"-{_DEAD_PID}")


@pytest.mark.parametrize("ns, liveness", [
    pytest.param(_OTHER_BOOT_NS, True, id="another-kernels-lock-proves-nothing"),
    pytest.param(_OTHER_BOOT_NS, False, id="older-hermes-record"),
])
def test_another_hostname_without_proof_this_kernel_can_give_is_still_refused(fake, ns, liveness):
    """A VM behind a file share (Docker Desktop) does not carry ``flock``: its browser's lock looks free from here."""
    script, udd = fake
    record = _left_behind(udd, host="container-3f9c2a", ns=ns, liveness=liveness)

    browser, err = _launch(script, udd)

    assert browser is None and "another environment" in err
    assert pids(udd, "spawns") == []
    assert record.exists() and os.readlink(udd / "SingletonLock") == f"container-3f9c2a-{_DEAD_PID}"


@pytest.mark.live_system_guard_bypass  # the fake browser is ours to stop if a leg failed
def test_a_launched_browser_holds_its_liveness_lock_until_it_dies(fake):
    script, udd = fake
    browser, err = _launch(script, udd)
    assert err is None
    record = launcher._record_path(browser)

    assert holders.liveness_released(record) is False, "the launcher's exit would read as the browser's death"

    os.kill(browser.pid, signal.SIGKILL)  # windows-footgun: ok — a linux-only module (pytestmark)
    launcher._procs[browser.key].wait(timeout=10)
    assert holders.liveness_released(record) is True


def test_a_lock_the_executable_did_not_keep_is_removed_not_trusted(tmp_path):
    """A wrapper that closes inherited fds would otherwise read as a dead browser while it runs."""
    state = tmp_path / "state"
    state.mkdir()
    record = state / "launch.json"
    liveness = holders.bind_liveness(holders.open_liveness(state), record)

    holders.settle_liveness(liveness)

    assert holders.liveness_released(record) is None
