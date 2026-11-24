"""A stopped turn schedules no between-turns compaction pass (``compression.prepare_ahead``, #159715).

Stop cancels the turn's pending pass and its foreground compaction; the finalizer must not then start a
fresh summary request from the interrupted turn. A turn that completes still schedules one.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent import prepared_compaction as pc


def _response(*, tool_call_id: str | None = None, content=None):
    tool_calls = [SimpleNamespace(
        id=tool_call_id, type="function", function=SimpleNamespace(name="web_search", arguments='{"query": "q"}'),
    )] if tool_call_id else None
    msg = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=None, reasoning=None)
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason="tool_calls" if tool_calls else "stop")], model="test/model",
    )
    resp.usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=50, total_tokens=1050)
    return resp


@pytest.mark.parametrize("stopped", [True, False])
def test_turn_end_pass_is_scheduled_only_when_the_turn_was_not_stopped(stopped):
    from run_agent import AIAgent

    Path(os.environ["HERMES_HOME"], "config.yaml").write_text(
        "compression:\n  prepare_ahead: true\n", encoding="utf-8",
    )
    tool_defs = [{"type": "function", "function": {
        "name": "web_search", "description": "search", "parameters": {"type": "object", "properties": {}},
    }}]
    with (
        patch("model_tools.get_tool_definitions", return_value=tool_defs),
        patch("model_tools.check_toolset_requirements", return_value={}),
        patch("agent.process_bootstrap.OpenAI"),
    ):
        agent = AIAgent(api_key="test-key-1234567890", base_url="https://openrouter.ai/api/v1",
                        quiet_mode=True, skip_context_files=True, skip_memory=True)
    agent.client = MagicMock()
    agent._cached_system_prompt = "You are helpful."
    agent._use_prompt_caching = False
    agent.save_trajectories = False
    assert agent.context_compressor.prepared_compaction is not None

    def run_tool(*_a, **_k):
        if stopped:
            agent.interrupt("stop")  # the user's Stop lands while the tool runs
        return '{"ok": true}'

    agent.client.chat.completions.create.side_effect = [
        _response(tool_call_id="c1"), _response(content="done"),
    ]
    origins = []
    real = pc.maybe_prepare

    def spy(agent_, messages, *, origin):
        origins.append(origin)
        return real(agent_, messages, origin=origin)

    with (
        patch("model_tools.handle_function_call", side_effect=run_tool),
        patch.object(pc, "maybe_prepare", side_effect=spy),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("continue", conversation_history=[])

    assert result.get("interrupted", False) is stopped
    assert ("turn-end" in origins) is not stopped
