from __future__ import annotations

import asyncio

from app.web.websocket_bridge import bridge_websocket_frames


class FakeClient:
    def __init__(self) -> None:
        self.messages = [
            {"type": "websocket.receive", "bytes": b"client-frame"},
            {"type": "websocket.disconnect"},
        ]
        self.sent: list[bytes | str] = []

    async def receive(self) -> dict[str, object]:
        await asyncio.sleep(0)
        return self.messages.pop(0)

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


class FakeUpstream:
    def __init__(self) -> None:
        self.received: list[bytes | str] = []
        self.frame_sent = asyncio.Event()

    async def send(self, data: bytes | str) -> None:
        self.received.append(data)
        self.frame_sent.set()

    def __aiter__(self):  # noqa: ANN201
        return self

    async def __anext__(self) -> bytes:
        await self.frame_sent.wait()
        if self.frame_sent.is_set():
            self.frame_sent.clear()
            return b"server-frame"
        raise StopAsyncIteration


def test_websocket_bridge_forwards_binary_frames_and_cleans_up() -> None:
    async def run() -> tuple[FakeClient, FakeUpstream]:
        client = FakeClient()
        upstream = FakeUpstream()
        await bridge_websocket_frames(client, upstream)
        return client, upstream

    client, upstream = asyncio.run(run())
    assert upstream.received == [b"client-frame"]
    assert client.sent == [b"server-frame"]
