"""Custom-endpoint API keys are provider credentials in every child env (#147329).

Hermes mints their env names itself (``custom_endpoint_key_env``), one per endpoint, so the
provider registry never lists them; the terminal must still keep them out of its children and
out of the persisted session snapshot, while an inheriting child can still reach the endpoint.
"""

import os
from pathlib import Path

from hermes_cli.config import custom_endpoint_key_env
from tests.tools import _child_env_fixtures
from tests.tools._child_env_fixtures import observe_child, observe_terminal
from tools.environments import local

child_env = _child_env_fixtures.child_env

SECRET = "sk-custom-endpoint-secret-147329"
CUSTOM_KEYS = [custom_endpoint_key_env("my endpoint"), custom_endpoint_key_env("127.0.0.1:1234"),
               custom_endpoint_key_env("")]
# The user's own names that only resemble the shape: not Hermes credentials, so they pass.
OPERATOR_VARS = ["HERMES_CUSTOM_BANNER", "MY_CUSTOM_API_KEY"]


def test_terminal_child_and_snapshot_never_hold_custom_endpoint_keys(child_env, monkeypatch):
    for name in [*CUSTOM_KEYS, *OPERATOR_VARS]:
        monkeypatch.setenv(name, SECRET if name in CUSTOM_KEYS else "operator-" + name)
    env = local.LocalEnvironment(cwd=str(child_env), timeout=30)
    try:
        env.execute("true")  # refreshes the session snapshot like any agent command
        observed = observe_terminal(env, [*CUSTOM_KEYS, *OPERATOR_VARS])
        snapshot = Path(env._snapshot_path).read_text(encoding="utf-8-sig")
    finally:
        env.cleanup()
    assert observed == {**dict.fromkeys(CUSTOM_KEYS), **{k: "operator-" + k for k in OPERATOR_VARS}}
    assert SECRET not in snapshot


def test_background_and_nonterminal_children_drop_custom_endpoint_keys(child_env, monkeypatch):
    for name in CUSTOM_KEYS:
        monkeypatch.setenv(name, SECRET)
    for built in (local._sanitize_subprocess_env(dict(os.environ)), local.hermes_subprocess_env()):
        assert observe_child(built, CUSTOM_KEYS) == dict.fromkeys(CUSTOM_KEYS)


def test_inheriting_child_keeps_custom_endpoint_keys(child_env, monkeypatch):
    for name in CUSTOM_KEYS:
        monkeypatch.setenv(name, SECRET)
    observed = observe_child(local.hermes_subprocess_env(inherit_credentials=True), CUSTOM_KEYS)
    assert observed == dict.fromkeys(CUSTOM_KEYS, SECRET)
