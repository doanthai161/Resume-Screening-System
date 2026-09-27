import asyncio
from unittest.mock import AsyncMock

import pytest
from app.middleware.body_limit import BodyLimitMiddleware

pytestmark = pytest.mark.asyncio


async def test_auth_size_limit_uses_configured_prefix_not_content_type():
    middleware = BodyLimitMiddleware(AsyncMock(), 2 * 1024 * 1024, api_prefix="/custom")
    receive, send = AsyncMock(), AsyncMock()
    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/custom/register/login",
            "headers": [
                (b"content-type", b"multipart/form-data"),
                (b"content-length", b"16385"),
            ],
        },
        receive,
        send,
    )
    assert send.call_args_list[0].args[0]["status"] == 413
    receive.assert_not_awaited()


async def test_upload_saturation_does_not_block_auth_or_json():
    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})

    middleware = BodyLimitMiddleware(app, 1024)
    gate, ready = asyncio.Event(), asyncio.Event()
    entered = 0

    async def stalled():
        nonlocal entered
        entered += 1
        if entered == 16:
            ready.set()
        await gate.wait()
        return {"type": "http.disconnect"}

    tasks = [
        asyncio.create_task(
            middleware(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/api/v1/resumes",
                    "headers": [],
                    "client": (f"client-{i}", 1),
                },
                stalled,
                AsyncMock(),
            )
        )
        for i in range(16)
    ]
    try:
        await asyncio.wait_for(ready.wait(), 2)
        for path in [
            "/api/v1/register/login",
            "/api/v1/register/refresh",
            "/api/v1/register/logout",
            "/api/v1/users/bulk/update",
        ]:
            send = AsyncMock()
            await middleware(
                {
                    "type": "http",
                    "method": "POST",
                    "path": path,
                    "headers": [],
                    "client": ("legitimate", 1),
                },
                AsyncMock(return_value={"type": "http.request", "body": b"{}"}),
                send,
            )
            assert send.call_args_list[0].args[0]["status"] == 200
    finally:
        gate.set()
        await asyncio.gather(*tasks)
    assert not middleware.clients
    assert not any(middleware.active.values())


async def test_same_client_rejected_before_body_read_other_clients_work():
    middleware = BodyLimitMiddleware(AsyncMock(), 1024)
    gate, ready = asyncio.Event(), asyncio.Event()
    entered = 0

    async def stalled():
        nonlocal entered
        entered += 1
        if entered == 2:
            ready.set()
        await gate.wait()
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/register/login",
        "headers": [],
        "client": ("attacker", 1),
    }
    tasks = [
        asyncio.create_task(middleware(scope, stalled, AsyncMock())) for _ in range(2)
    ]
    try:
        await asyncio.wait_for(ready.wait(), 2)
        receive, send = AsyncMock(), AsyncMock()
        await middleware(scope, receive, send)
        receive.assert_not_awaited()
        assert send.call_args_list[0].args[0]["status"] == 429
        await middleware(
            {**scope, "client": ("other", 2)},
            AsyncMock(return_value={"type": "http.request", "body": b"{}"}),
            AsyncMock(),
        )
        middleware.app.assert_awaited_once()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert not middleware.clients


async def test_slot_released_after_replay_not_after_slow_downstream():
    async def app(scope, receive, send):
        await receive()
        assert not middleware.clients
        assert not any(middleware.active.values())
        raise RuntimeError("downstream failure")

    middleware = BodyLimitMiddleware(app, 1024)
    with pytest.raises(RuntimeError):
        await middleware(
            {"type": "http", "method": "POST", "headers": []},
            AsyncMock(return_value={"type": "http.request", "body": b"{}"}),
            AsyncMock(),
        )
    assert not middleware.clients


@pytest.mark.parametrize("length", [b"-1", b"invalid"])
async def test_invalid_length_rejected_without_allocating_capacity(length):
    middleware = BodyLimitMiddleware(AsyncMock(), 1024)
    receive, send = AsyncMock(), AsyncMock()
    await middleware(
        {"type": "http", "method": "POST", "headers": [(b"content-length", length)]},
        receive,
        send,
    )
    assert send.call_args_list[0].args[0]["status"] == 400
    receive.assert_not_awaited()
    assert not middleware.clients


@pytest.mark.parametrize(
    "headers,messages",
    [
        ([(b"content-length", b"11")], []),
        (
            [],
            [
                {"type": "http.request", "body": b"123456", "more_body": True},
                {"type": "http.request", "body": b"78901", "more_body": False},
            ],
        ),
        ([(b"content-length", b"1")], [{"type": "http.request", "body": b"x" * 11}]),
    ],
)
async def test_reject_before_parser_including_chunked_and_false_length(
    headers, messages
):
    app, send = AsyncMock(), AsyncMock()
    receive = AsyncMock(side_effect=messages)
    await BodyLimitMiddleware(app, 10)(
        {"type": "http", "method": "POST", "headers": headers}, receive, send
    )
    app.assert_not_awaited()
    assert send.call_args_list[0].args[0]["status"] == 413


async def test_body_is_replayed_exactly():
    received = []

    async def app(scope, receive, send):
        received.append(await receive())

    receive = AsyncMock(
        side_effect=[
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ]
    )
    await BodyLimitMiddleware(app, 6)(
        {"type": "http", "method": "POST", "headers": []}, receive, AsyncMock()
    )
    assert received == [{"type": "http.request", "body": b"abcdef", "more_body": False}]


async def test_slow_body_times_out_before_parser():
    async def receive():
        await asyncio.sleep(1)

    app, send = AsyncMock(), AsyncMock()
    await BodyLimitMiddleware(app, 10, timeout=0.01)(
        {"type": "http", "method": "POST", "headers": []}, receive, send
    )
    app.assert_not_awaited()
    assert send.call_args_list[0].args[0]["status"] == 408
