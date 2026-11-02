"""Cross-process holders of a shared-profile Chromium (``tools/browser_launcher.py``).

A persistent profile (``browser.user_data_dir``, the Bot Desktop profile, the real-profile copy) has ONE
Chromium, launched by whichever Hermes process got there first; every other process attaches to it. Each
attacher leaves a lease beside the launch record in the profile's private state dir (per OS user, so every
Hermes home pinning the dir sees the same leases: ``browser_launcher._shared_profiles_root``). The process holding
the record (the launcher, or whoever it was handed to) stops the browser on exit only when no other live
Hermes process holds a lease; otherwise it hands the record to one of them, so the LAST holder stops it. A
holder that crashed is dropped once its pid and start time no longer match, never on the pid alone.

A pid means something only inside the PID namespace and boot that wrote it (a ``HERMES_HOME`` shared by a
container gateway and a host CLI): every record, lease and throwaway-dir name carries the writer's
:func:`ns_identity`, and one from another namespace or boot, or one lacking it, is never judged dead. A lease is
keyed by the record of the browser it holds, whichever namespace wrote that record; only its holder part carries
the lease writer's tag, so a launcher finds every holder whatever namespace it attached from. What does prove
another namespace's browser gone is a lock the kernel holds: a launch leaves a ``.alive`` file beside its record
whose shared ``flock`` the browser inherits, so it drops with the browser, whatever happened to the container.

Every decision runs under the records dir's lock: a lease written while another process decides cannot be
missed. Siblings late-import the facade (its ``_pid_alive`` / ``_start_time`` are the patched seams).
"""

from __future__ import annotations

import contextlib
import errno
import functools
import glob
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

_LOCK_NAME = ".holders.lock"


@functools.cache
def ns_identity() -> Optional[str]:
    """This process's PID namespace and boot (Linux: the pid-namespace inode + the kernel boot id); None where
    neither is readable (no ``/proc``), which then matches only another None."""
    try:
        ino = os.stat("/proc/self/ns/pid").st_ino
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        return None
    return f"{ino}:{boot}"


def ns_tag() -> str:
    """The name suffix of this namespace's records, leases and throwaway dirs (``""`` without an identity)."""
    identity = ns_identity()
    return f"-n{hashlib.sha256(identity.encode()).hexdigest()[:12]}" if identity else ""


def same_namespace(data: dict) -> bool:
    """A record or lease written in this PID namespace and boot: only then do its pids name processes here."""
    return data.get("ns") == ns_identity()


def held_here(data: dict, pid_field: str) -> bool:
    return data.get(pid_field) == os.getpid() and same_namespace(data)


# msvcrt.locking reports a lock another process holds as EDEADLOCK (LK_LOCK, after its own ~10 s of retries)
# or EACCES (LK_NBLCK); anything else is a real error.
_NT_LOCK_CONTENDED = frozenset({errno.EACCES, errno.EDEADLK, getattr(errno, "EDEADLOCK", errno.EDEADLK)})
_NT_LOCK_TIMEOUT_S = 30.0


def nt_lock_failure(exc: OSError, records: Path, expired: bool) -> Optional[OSError]:
    """What a failed ``msvcrt.locking`` attempt means: None to retry (the lock is held and time is left), else
    the error to raise. Bounded, so a lock leaked by a hung process fails the caller instead of hanging it."""
    if exc.errno not in _NT_LOCK_CONTENDED:
        return exc
    if expired:
        return TimeoutError(f"browser launcher state {records} stayed locked for {_NT_LOCK_TIMEOUT_S:.0f} s")
    return None


def _lock_nt(fd: int, records: Path, blocking: bool) -> bool:
    import msvcrt
    deadline = time.monotonic() + _NT_LOCK_TIMEOUT_S
    while True:
        try:
            msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            failure = nt_lock_failure(exc, records, not blocking or time.monotonic() >= deadline)
            if failure is exc:
                raise
            if not blocking:
                return False
            if failure is not None:
                raise failure from exc


def _lock_posix(fd: int, blocking: bool) -> bool:
    import fcntl  # windows-footgun: ok — POSIX only (records_lock's nt branch takes _lock_nt)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


@contextlib.contextmanager
def records_lock(records: Path, *, blocking: bool = True) -> Iterator[bool]:
    """Exclusive, cross-process lock on ``records`` (a private launcher state dir); yields whether it is held
    (always True when ``blocking``). Never nested: two locks on one file in one process are two open file
    descriptions, and the second would wait forever."""
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(records / _LOCK_NAME, flags, 0o600)
    held = False
    try:
        held = _lock_nt(fd, records, blocking) if os.name == "nt" else _lock_posix(fd, blocking)
        yield held
    finally:
        if held and os.name == "nt":
            import msvcrt
            with contextlib.suppress(OSError):
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        os.close(fd)


def _lease_path(record: Path, holder_pid: int) -> Path:
    return record.with_name(f"{record.stem}@{holder_pid}{ns_tag()}.lease")


def _load(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        return data
    path.unlink(missing_ok=True)
    return None


def process_alive(pid, start_time) -> bool:
    """``pid`` is still the process that wrote a record or lease: alive, and with the recorded start time
    (a recycled pid has another one)."""
    from tools import browser_launcher as launcher
    if not isinstance(pid, int) or not launcher._pid_alive(pid):
        return False
    return start_time is None or launcher._start_time(pid) == start_time


def write_lease(record: Path, browser) -> None:
    """This process holds ``browser`` (caller holds :func:`records_lock`)."""
    from tools import browser_launcher as launcher
    from utils import atomic_json_write
    me = os.getpid()
    atomic_json_write(_lease_path(record, me), {
        "holder_pid": me, "holder_start_time": launcher._start_time(me), "pid": browser.pid,
        "start_time": browser.start_time, "user_data_dir": browser.user_data_dir, "ns": ns_identity()}, indent=None)


def drop_lease(record: Path) -> None:
    _lease_path(record, os.getpid()).unlink(missing_ok=True)


def _holder_gone(lease: dict) -> bool:
    """Proven gone: written in this namespace and boot, and its pid + start time no longer match."""
    return same_namespace(lease) and not process_alive(lease.get("holder_pid"), lease.get("holder_start_time"))


def other_live_holders(record: Path, browser_start_time) -> list[dict]:
    """Leases of OTHER live processes on the browser ``record`` names (caller holds :func:`records_lock`).
    A lease of a dead holder, or one naming an earlier browser on the same pid (another start time, from whichever
    namespace that knew it), is removed; one from another namespace counts as live (its holder cannot be checked
    from here)."""
    holders = []
    for path in sorted(record.parent.glob(f"{record.stem}@*.lease")):
        lease = _load(path)
        if lease is None or held_here(lease, "holder_pid"):
            continue
        other_launch = lease.get("start_time") != browser_start_time and (
            same_namespace(lease) or lease.get("start_time") is not None)
        if other_launch or _holder_gone(lease):
            path.unlink(missing_ok=True)
            continue
        holders.append(lease)
    return holders


def open_liveness(records: Path) -> Optional[tuple[int, Path]]:
    """``(fd, path)`` of a new file under a shared ``flock``, for a browser about to launch to inherit ``fd``: the
    lock then lives exactly as long as that browser. None where it cannot be had (Windows, no ``flock`` there)."""
    if os.name == "nt":
        return None
    import fcntl  # windows-footgun: ok — POSIX only (nt returned above)
    path = records / f".starting-{os.getpid()}-{time.monotonic_ns()}.alive"
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        path.unlink(missing_ok=True)
        return None
    return fd, path


def _liveness_path(record: Path) -> Path:
    return record.with_suffix(".alive")


def bind_liveness(liveness: Optional[tuple[int, Path]], record: Optional[Path]) -> Optional[tuple[int, Path]]:
    """Move a launch's liveness file beside its launch record, whose name carries the pid."""
    if liveness is None or record is None:
        return liveness
    fd, path = liveness
    try:
        path.rename(_liveness_path(record))
    except OSError as exc:
        logger.debug("browser: could not bind the liveness lock of %s: %s", record.name, exc)
        return liveness
    return fd, _liveness_path(record)


def settle_liveness(liveness: Optional[tuple[int, Path]]) -> None:
    """Drop the launcher's own copy of the lock. A file nobody else holds proves nothing (the launch failed, or the
    executable is a wrapper that closes inherited fds), so it goes: its record then counts as one without proof."""
    if liveness is None:
        return
    fd, path = liveness
    os.close(fd)
    if _flock_free(path):
        path.unlink(missing_ok=True)


def _flock_free(path: Path) -> bool:
    import fcntl  # windows-footgun: ok — POSIX only (open_liveness makes no such file on nt)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    finally:
        os.close(fd)
    return True


def liveness_released(record: Path) -> Optional[bool]:
    """Whether the browser ``record`` names let go of its liveness lock; None when the launch left no such file
    (written by an older Hermes, or the browser did not keep the fd). An unreadable file is not proof of anything."""
    try:
        return _flock_free(_liveness_path(record))
    except FileNotFoundError:
        return None
    except OSError:
        return False


def same_boot(data: dict) -> bool:
    """A record written under this kernel: only there is a free ``flock`` proof (a share to a VM does not carry it)."""
    here, theirs = ns_identity(), data.get("ns")
    return isinstance(here, str) and isinstance(theirs, str) and here.rpartition(":")[2] == theirs.rpartition(":")[2]


def forget_launch(record: Path) -> None:
    """Remove every trace of a launch whose browser is gone (caller holds :func:`records_lock`)."""
    for lease in record.parent.glob(f"{glob.escape(record.stem)}@*.lease"):
        lease.unlink(missing_ok=True)
    _liveness_path(record).unlink(missing_ok=True)
    record.unlink(missing_ok=True)


def drop_orphan_liveness(records: Path) -> None:
    """Liveness files whose launch record is gone (caller holds :func:`records_lock`, which a launch holds too)."""
    for path in records.glob("*.alive"):
        if not path.with_suffix(".json").exists():
            path.unlink(missing_ok=True)


def drop_dead_leases(records: Path) -> None:
    """Leases whose holder is gone (caller holds :func:`records_lock`): a lease outlives its launch record when
    the browser died while its holder crashed."""
    for path in records.glob("*.lease"):
        lease = _load(path)
        if lease is not None and _holder_gone(lease):
            path.unlink(missing_ok=True)


def successor(holders: list[dict]) -> Optional[dict]:
    """The live holder a record can be handed to: one in this namespace (a pid from another one names nobody
    here, so a record handed there could never be stopped nor reaped from here)."""
    return next((h for h in holders if same_namespace(h)), None)


def hand_over(record: Path, data: dict, holder: dict) -> None:
    """Make ``holder`` the record's owner: every reaper now sees a live owner, and the holder stops the
    browser when it is the last one out."""
    from utils import atomic_json_write
    atomic_json_write(record, {**data, "owner_pid": holder["holder_pid"],
                               "owner_start_time": holder.get("holder_start_time")}, indent=None)
    logger.info("browser: Chromium pid %s is still used by Hermes pid %s; handed it over instead of stopping it",
                data.get("pid"), holder["holder_pid"])
