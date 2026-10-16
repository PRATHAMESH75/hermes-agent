"""A follow-up queued during a restart drain is acknowledged once, however often it bounces (#139911).

While the gateway drains for a restart, the queued follow-up is re-dispatched every time the running
turn's task finishes a pass, finds the session still busy and is queued again (#123229). Each bounce
used to send the "queued for the next turn" notice again: 235 notices to one Slack thread in one
restart wait.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult, SessionSource
from gateway.platforms.event import MessageEvent, MessageType
from gateway.run import GatewayRunner


class _Adapter(BasePlatformAdapter):
    async def connect(self, *, is_reconnect: bool = False) -> bool:
        return True

    async def disconnect(self):
        pass

    async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
        return SendResult(success=True)

    async def get_chat_info(self, chat_id):
        return {}


def _draining_runner(adapter) -> GatewayRunner:
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig()
    runner._busy_input_mode = "queue"
    runner._busy_text_mode = "queue"
    runner._profile_adapters = {}
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._sessions = {}
    runner._draining = True
    runner._restart_requested = True
    runner.session_store = None
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()
    runner.pairing_store = MagicMock()
    runner.pairing_store.is_approved.return_value = True
    runner._is_user_authorized = lambda source: True
    runner._session_has_compression_in_flight = AsyncMock(return_value=False)
    return runner


def _event(message_id: str) -> MessageEvent:
    return MessageEvent(
        text="follow up",
        message_type=MessageType.TEXT,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="chat-1", chat_type="dm", user_id="user-1"),
        message_id=message_id,
    )


@pytest.fixture
def drain(monkeypatch):
    monkeypatch.setenv("HERMES_TELEGRAM_FOLLOWUP_GRACE_SECONDS", "0")
    adapter = _Adapter(PlatformConfig(enabled=True, token="test-token"), Platform.TELEGRAM)
    runner = _draining_runner(adapter)
    event = _event("message-1")
    session_key = runner._session_key_for_source(event.source)
    agent = MagicMock()
    agent._active_children = []
    runner._running_agents[session_key] = agent
    return runner, adapter, event, session_key


@pytest.mark.asyncio
async def test_priority_path_acknowledges_a_bouncing_drain_event_once(drain):
    runner, adapter, event, session_key = drain

    first = await runner._handle_message(event)
    assert isinstance(first, str) and "queued" in first
    for _ in range(3):  # the base adapter re-dispatches the pending slot after each pass
        adapter._pending_messages.pop(session_key)
        assert await runner._handle_message(event) is None
        assert adapter._pending_messages[session_key] is event  # still queued for the next process

    # A genuinely new follow-up is still told it was queued.
    adapter._pending_messages.pop(session_key)
    second = await runner._handle_message(_event("message-2"))
    assert isinstance(second, str) and "queued" in second


@pytest.mark.asyncio
async def test_busy_handler_path_acknowledges_a_bouncing_drain_event_once(drain):
    runner, adapter, event, session_key = drain
    sent = []
    runner._send_busy_reply = AsyncMock(side_effect=lambda ev, ad, content, **_: sent.append(content))

    for _ in range(3):
        adapter._pending_messages.pop(session_key, None)
        assert await runner._handle_active_session_busy_message(event, session_key) is True
        assert adapter._pending_messages[session_key] is event

    assert len(sent) == 1 and "queued" in sent[0]
