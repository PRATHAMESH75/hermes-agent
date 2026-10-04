"""A cached computer_use backend is bound to the display it spawned on; the identity helpers notice a change."""

from __future__ import annotations

import sys

import pytest

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from tools.computer_use import cua_backend


def test_backend_display_identity_tracks_the_display_a_spawn_would_get():
    before = cua_backend.desktop_identity({"HOME": "/x"})  # no screen yet
    after = cua_backend.desktop_identity({"HOME": "/x", "DISPLAY": ":37"})  # Bot Desktop came up
    assert before == "" and after == ":37"
    assert cua_backend.backend_display_stale(before, after)
    assert not cua_backend.backend_display_stale(after, cua_backend.desktop_identity({"DISPLAY": ":37"}))


_NATIVE = {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-1", "XDG_RUNTIME_DIR": "/run/user/1000",
           "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus", "AT_SPI_BUS_ADDRESS": "unix:path=/tmp/at-spi-a",
           cua_backend._CUA_NATIVE_WAYLAND_ENV_VAR: "1"}


@pytest.mark.parametrize("key", cua_backend._NATIVE_SESSION_ENDPOINT_KEYS)
def test_native_identity_changes_with_session_endpoints_on_the_same_display(key):
    moved = {**_NATIVE, key: _NATIVE[key] + "-restarted"}
    assert cua_backend.backend_display_stale(cua_backend.desktop_identity(_NATIVE), cua_backend.desktop_identity(moved))


def test_native_identity_ignores_unrelated_keys_and_x11_identity_stays_the_display():
    noisy = {**_NATIVE, "TERM": "xterm", "PWD": "/elsewhere", "XAUTHORITY": "/tmp/x"}
    assert cua_backend.desktop_identity(noisy) == cua_backend.desktop_identity(dict(_NATIVE))
    x11 = {k: v for k, v in _NATIVE.items() if k != cua_backend._CUA_NATIVE_WAYLAND_ENV_VAR}
    assert cua_backend.desktop_identity(x11) == ":0"  # bridge off: legacy DISPLAY-only contract
    assert cua_backend.desktop_identity({"WAYLAND_DISPLAY": "wayland-1"}) == ""
    assert cua_backend.desktop_identity(_NATIVE) != ":0"


@pytest.fixture
def native_profiles(tmp_path, monkeypatch):
    import tools.computer_use.tool as cu

    homes = [tmp_path / "a", tmp_path / "b"]
    for home in homes:
        home.mkdir()
        (home / "config.yaml").write_text("computer_use:\n  native_wayland: true\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(homes[0]))
    monkeypatch.setattr(sys, "platform", "linux")
    for key, value in _NATIVE.items():
        if key != cua_backend._CUA_NATIVE_WAYLAND_ENV_VAR:
            monkeypatch.setenv(key, value)
    created = []

    class _Backend:
        def __init__(self, mode):
            self.stopped = False
            created.append(self)

        def start(self):
            pass

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(cu, "_new_backend", _Backend)
    monkeypatch.setattr(cu, "_cua_permission_mode", lambda sid: "standard")
    with cu._backend_lock:
        cu._backends.clear(), cu._backend_call_locks.clear(), cu._backend_permission_modes.clear()
        cu._backend_displays.clear()
    yield cu, homes
    with cu._backend_lock:
        cu._backends.clear(), cu._backend_call_locks.clear(), cu._backend_permission_modes.clear()
        cu._backend_displays.clear()


def _get_under(cu, home):
    token = set_hermes_home_override(str(home))
    try:
        return cu._get_backend("shared")
    finally:
        reset_hermes_home_override(token)


def test_session_bus_change_rebinds_only_the_calling_profile_a_to_b_to_a(native_profiles, monkeypatch):
    cu, (a, b) = native_profiles
    a1, b1 = _get_under(cu, a), _get_under(cu, b)
    assert a1 is not b1
    monkeypatch.setenv("TERM", "dumb")  # unrelated key: established backends are reused
    assert _get_under(cu, a) is a1 and _get_under(cu, b) is b1

    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus-2")  # same DISPLAY, new session
    a2 = _get_under(cu, a)
    assert a2 is not a1 and a1.stopped
    assert not b1.stopped  # B's backend is B's to retire
    b2 = _get_under(cu, b)
    assert b2 is not b1 and b1.stopped and not a2.stopped
    assert _get_under(cu, a) is a2

    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", _NATIVE["DBUS_SESSION_BUS_ADDRESS"])  # back to session A
    a3 = _get_under(cu, a)
    assert a3 is not a2 and a2.stopped and not b2.stopped
