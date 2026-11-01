"""A reaction's session source takes its chat kind from Feishu's ``chat_mode``.

``GET im/v1/chats/:id`` answers ``chat_mode`` (p2p/group/topic) for the conversation kind and
``chat_type`` (private/public) for group visibility. ``GET im/v1/messages/:id`` carries no chat kind,
so the chat lookup is the only thing keeping a group reaction out of a DM session.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gateway.config import PlatformConfig


def _reaction_adapter(chat_data):
    from plugins.platforms.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter(PlatformConfig())
    adapter._app_id = "cli_self_app"
    message = SimpleNamespace(sender=SimpleNamespace(id="cli_self_app"), chat_id="oc_chat")
    adapter._client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(
        message=SimpleNamespace(get=Mock(return_value=SimpleNamespace(
            success=lambda: True, data=SimpleNamespace(items=[message])))),
        chat=SimpleNamespace(get=Mock(return_value=SimpleNamespace(success=lambda: True, data=chat_data))),
    )))
    adapter._build_get_message_request = Mock(return_value=object())
    adapter._build_get_chat_request = Mock(return_value=object())
    adapter._handle_message_with_guards = AsyncMock()
    adapter._resolve_sender_profile = AsyncMock(
        return_value={"user_id": "u_human", "user_name": "Human", "user_id_alt": None})
    return adapter


@pytest.mark.parametrize(("chat_mode", "visibility", "expected"), [
    ("group", "private", "group"),
    ("group", "public", "group"),
    ("topic", "private", "forum"),
    ("p2p", "", "dm"),
])
def test_reaction_source_follows_chat_mode(chat_mode, visibility, expected):
    adapter = _reaction_adapter(SimpleNamespace(name="Team", chat_mode=chat_mode, chat_type=visibility))
    event = SimpleNamespace(
        message_id="om_bot", user_id=SimpleNamespace(open_id="ou_human"),
        reaction_type=SimpleNamespace(emoji_type="THUMBSUP"))

    asyncio.run(adapter._handle_reaction_event("im.message.reaction.created_v1", SimpleNamespace(event=event)))

    synthetic = adapter._handle_message_with_guards.await_args.args[0]
    assert synthetic.text == "reaction:added:THUMBSUP"
    assert synthetic.source.chat_type == expected
