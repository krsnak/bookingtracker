"""Small, dependency-free relay of noVNC WebSocket frames to websockify."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol


class ClientWebSocket(Protocol):
    async def receive(self) -> dict[str, object]: ...

    async def send_bytes(self, data: bytes) -> None: ...

    async def send_text(self, data: str) -> None: ...


class UpstreamWebSocket(Protocol):
    async def send(self, data: bytes | str) -> None: ...

    def __aiter__(self) -> AsyncIterator[bytes | str]: ...


async def bridge_websocket_frames(client: ClientWebSocket, upstream: UpstreamWebSocket) -> None:
    """Relay text/binary frames until either side closes, then cancel the peer task."""

    async def client_to_upstream() -> None:
        while True:
            message = await client.receive()
            if message["type"] == "websocket.disconnect":
                return
            if isinstance(message.get("bytes"), bytes):
                await upstream.send(message["bytes"])
            elif isinstance(message.get("text"), str):
                await upstream.send(message["text"])

    async def upstream_to_client() -> None:
        async for message in upstream:
            if isinstance(message, bytes):
                await client.send_bytes(message)
            else:
                await client.send_text(message)

    tasks = [
        asyncio.create_task(client_to_upstream()),
        asyncio.create_task(upstream_to_client()),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
