"""A dashboard profile action (Skills -> Update, ...) runs on a request that binds no secret scope.

Under ``gateway.multiplex_profiles`` an unscoped ``terminal.env_passthrough`` read fails closed, so
building the child's env raised ``UnscopedSecretError`` on every attempt and the child never started
(#147694). The values must come from the target profile's scope, never the launch profile's.
"""

from __future__ import annotations

import pytest


TOKEN = "MY_SERVICE_TOKEN"


@pytest.fixture
def homes(tmp_path, monkeypatch):
    root = tmp_path / "hermes"
    coder = root / "profiles" / "coder"
    coder.mkdir(parents=True)
    (root / "config.yaml").write_text(f"terminal:\n  env_passthrough: [{TOKEN}]\n", encoding="utf-8")
    (coder / "config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.delenv(TOKEN, raising=False)
    import hermes_constants
    monkeypatch.setattr(hermes_constants, "_default_hermes_root_memo", None)
    from tui_gateway import launch_profile_policy
    monkeypatch.setattr(launch_profile_policy, "_snapshot", None)
    from tools.env_passthrough import clear_env_passthrough
    clear_env_passthrough()
    yield root, coder
    clear_env_passthrough()


@pytest.fixture
def multiplexed(monkeypatch):
    from agent import secret_scope
    monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)


def _action_env(profile: str) -> dict[str, str]:
    from hermes_cli.web_server_gateway import _profile_action_environment
    return _profile_action_environment(["-p", profile, "skills", "update"])


@pytest.mark.parametrize("profile", ["default", "coder"])
def test_action_spawns_when_the_launch_dotenv_declares_a_passthrough_secret(homes, multiplexed, monkeypatch, profile):
    root, _coder = homes
    (root / ".env").write_text(f"{TOKEN}=launch-value\n", encoding="utf-8")
    monkeypatch.setenv(TOKEN, "launch-value")
    env = _action_env(profile)
    # The child loads its own .env on startup, so the dashboard's value is not forwarded.
    assert TOKEN not in env


def test_named_target_never_borrows_an_env_only_launch_secret(homes, multiplexed, monkeypatch):
    """Injected by systemd / ``op run``: in no .env, so only the scope can keep it out of a sibling."""
    monkeypatch.setenv(TOKEN, "launch-value")
    assert TOKEN not in _action_env("coder")


def test_named_target_resolves_its_own_value(homes, multiplexed, monkeypatch):
    _root, coder = homes
    (coder / ".env").write_text(f"{TOKEN}=coder-value\n", encoding="utf-8")
    monkeypatch.setenv(TOKEN, "launch-value")
    assert _action_env("coder")[TOKEN] == "coder-value"


def test_launch_target_keeps_its_injected_secret(homes, multiplexed, monkeypatch):
    monkeypatch.setenv(TOKEN, "launch-value")
    assert _action_env("default")[TOKEN] == "launch-value"


def test_single_profile_host_keeps_forwarding_the_process_value(homes, monkeypatch):
    monkeypatch.setenv(TOKEN, "launch-value")
    assert _action_env("coder")[TOKEN] == "launch-value"


@pytest.mark.parametrize("argv", [["gateway", "restart"], ["-p", "default", "gateway", "restart"]])
def test_host_gateway_verbs_build_their_env_with_a_passthrough_secret_declared(homes, multiplexed, monkeypatch, argv):
    from hermes_cli.web_server_gateway import _profile_action_environment
    root, _coder = homes
    monkeypatch.setenv(TOKEN, "launch-value")
    assert _profile_action_environment(argv)["HERMES_HOME"] == str(root)
