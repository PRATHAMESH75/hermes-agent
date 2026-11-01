"""The loop-tick witness survives a HERMES_HOME that refuses AF_UNIX sockets (#146919)."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import gateway.shutdown_watchdog as shutdown_watchdog_module
import pytest

from gateway.shutdown_watchdog import get_loop_heartbeat_path, loop_heartbeat_forever
from tests.waiting import async_wait_until


@pytest.fixture()
def short_home():
    """pytest's tmp_path can overflow sun_path, which takes the TCP arm without ever reaching bind()."""
    path = Path(tempfile.mkdtemp(prefix="hlt-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.mark.platforms("posix")
@pytest.mark.asyncio
async def test_witness_answers_over_tcp_when_the_home_refuses_unix_sockets(short_home):
    """A home on 9p/drvfs fits sun_path but bind() answers EOPNOTSUPP: the gateway must keep a
    witness that really answers, or a wedged loop never escalates."""
    refused = OSError(errno.EOPNOTSUPP, "Operation not supported")
    with patch.object(shutdown_watchdog_module.asyncio, "start_unix_server", side_effect=refused) as start_unix_server:
        task = asyncio.create_task(loop_heartbeat_forever(interval_s=1.0, home=short_home))
        try:
            path = get_loop_heartbeat_path(short_home)
            await async_wait_until(path.exists, what="first heartbeat")
            start_unix_server.assert_called_once()
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            assert payload["loop_tick_socket"] is True
            reader, writer = await asyncio.open_connection("127.0.0.1", int(payload["loop_tick_tcp_port"]))
            assert await reader.read(1) == b"1"
            writer.close()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
