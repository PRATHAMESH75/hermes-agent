"""A sandbox-placed Bot Screen keeps Xfce's settings across restarts, as gateway placement does."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from tools.bot_desktop import sandbox_host
from tools.environments import streams


class _Env:
    def __init__(self):
        self._hermes_backend_name = "docker"
        self._container_id = "c0ffee"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Real bash for the seed and stop scripts on a throwaway home and tmp; only the launcher is simulated."""
    home, scratch = tmp_path / "home", tmp_path / "scratch"
    home.mkdir()
    scratch.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    spawned: list[dict] = []
    up = {"display": False}

    def run_in(env, argv, *, child_env=None, user=None, timeout=None):
        if "setsid" in argv[-1]:
            spawned.append(dict(child_env))
            up["display"] = True
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return subprocess.run(argv, env={**os.environ, "HOME": str(home)}, capture_output=True, timeout=timeout, check=False)

    monkeypatch.setattr(streams, "run_in", run_in)
    monkeypatch.setattr(streams, "sandbox_temp_dir", lambda env: str(scratch))
    monkeypatch.setattr(sandbox_host, "_user_for", lambda env: None)
    monkeypatch.setattr(sandbox_host, "missing_binaries", lambda env: [])
    monkeypatch.setattr(sandbox_host, "_published", lambda env, rdir: {"DISPLAY": ":20"} if up["display"] else {})
    return spawned, up, home


def test_xfce_settings_survive_a_screen_restart(sandbox):
    spawned, up, home = sandbox
    env = _Env()

    sandbox_host.start(env, "default", geometry="1280x800")
    setting = Path(spawned[-1]["HERMES_BD_CONFIG_HOME"]) / "xfce4" / "xfconf" / "xfce4-desktop.xml"
    setting.parent.mkdir(parents=True)
    setting.write_text("<wallpaper/>", encoding="utf-8")

    sandbox_host.stop(env, "default")
    up["display"] = False
    sandbox_host.start(env, "default", geometry="1280x800")

    assert Path(spawned[-1]["HERMES_BD_CONFIG_HOME"]) == setting.parents[2]
    assert setting.read_text(encoding="utf-8-sig") == "<wallpaper/>"
    assert setting.is_relative_to(home)


def test_settings_stay_private_to_the_desktop_user(sandbox):
    spawned, _up, _home = sandbox
    sandbox_host.start(_Env(), "default", geometry="1280x800")
    assert stat.S_IMODE(os.stat(spawned[-1]["HERMES_BD_CONFIG_HOME"]).st_mode) == 0o700
