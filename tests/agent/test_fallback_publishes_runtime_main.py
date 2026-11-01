"""A mid-turn fallback must republish the runtime main, or "active main model" gates judge the
route that just failed instead of the one serving the turn (#147539)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import hermes_yaml as yaml
from agent import auxiliary_client as aux
from agent.turn_context import _publish_runtime_main
from hermes_constants import get_hermes_home
from run_agent import AIAgent
from tools.vision_tools import _should_use_native_vision_fast_path

_GO_URL = "https://opencode.ai/zen/go/v1"


def _make_agent(fallback_model):
    with (
        patch("model_tools.get_tool_definitions", return_value=[]),
        patch("model_tools.check_toolset_requirements", return_value={}),
        patch("agent.process_bootstrap.OpenAI"),
    ):
        agent = AIAgent(
            api_key="primary-key",
            provider="openai-codex",
            model="gpt-5.5",
            base_url="https://chatgpt.com/backend-api/codex",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            fallback_model=fallback_model,
        )
    agent.client = None
    return agent


@pytest.fixture
def native_image_mode():
    (get_hermes_home() / "config.yaml").write_text(
        yaml.safe_dump({"agent": {"image_input_mode": "native"}}), encoding="utf-8"
    )
    yield
    aux.clear_runtime_main()


def test_activated_fallback_drives_the_vision_gate(native_image_mode):
    """opencode-go rejects tool-result images, so vision_analyze must leave the native lane the
    moment the turn moves onto it; embedding there downgrades the image to a text summary."""
    agent = _make_agent([{"provider": "opencode-go", "model": "deepseek-v4.1-flash"}])
    _publish_runtime_main(agent)
    assert _should_use_native_vision_fast_path() is True

    client = SimpleNamespace(base_url=_GO_URL, api_key="go-key")
    with patch("agent.auxiliary_client.resolve_provider_client", return_value=(client, "deepseek-v4.1-flash")):
        assert agent._try_activate_fallback(None) is True

    assert aux._read_main_provider() == "opencode-go"
    assert aux._read_main_model() == agent.model
    assert _should_use_native_vision_fast_path() is False


def test_unavailable_fallback_leaves_the_primary_published(native_image_mode):
    """No swap happened, so the primary still serves the turn and stays the runtime main."""
    agent = _make_agent([{"provider": "opencode-go", "model": "deepseek-v4.1-flash"}])
    _publish_runtime_main(agent)

    with patch("agent.auxiliary_client.resolve_provider_client", return_value=(None, None)):
        assert agent._try_activate_fallback(None) is False

    assert aux._read_main_provider() == "openai-codex"
    assert _should_use_native_vision_fast_path() is True
