from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from server.messaging.signal import SignalAdapter
from server.messaging.types import InboundMessage, ReplyTarget


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="http://signal.local",
        transport=httpx.MockTransport(handler),
    )


def test_signal_adapter_health_check_success() -> None:
    async def run_test() -> None:
        async with _client(lambda request: httpx.Response(200, json={"ok": True})) as client:
            adapter = SignalAdapter(
                account="+100",
                base_url="http://signal.local",
                allowed_senders={"+200"},
                client=client,
            )

            assert await adapter.health_check() is True

    asyncio.run(run_test())


def test_signal_adapter_health_check_failure_disables_adapter() -> None:
    async def run_test() -> None:
        async with _client(lambda request: httpx.Response(503)) as client:
            adapter = SignalAdapter(
                account="+100",
                base_url="http://signal.local",
                allowed_senders={"+200"},
                client=client,
            )

            assert await adapter.health_check() is False

    asyncio.run(run_test())


def test_signal_parse_data_message() -> None:
    adapter = SignalAdapter(
        account="+100",
        base_url="http://signal.local",
        allowed_senders={"+200"},
    )

    message = adapter.parse_event(
        {
            "envelope": {
                "sourceNumber": "+200",
                "dataMessage": {"message": "hello"},
            }
        }
    )

    assert message == InboundMessage(
        source="signal",
        sender="+200",
        text="hello",
        raw={
            "envelope": {
                "sourceNumber": "+200",
                "dataMessage": {"message": "hello"},
            }
        },
    )


def test_signal_ignores_empty_envelope() -> None:
    adapter = SignalAdapter(
        account="+100",
        base_url="http://signal.local",
        allowed_senders={"+200"},
    )

    assert adapter.parse_event({}) is None
    assert adapter.parse_event({"envelope": {"sourceNumber": "+200"}}) is None
    assert (
        adapter.parse_event(
            {"envelope": {"sourceNumber": "+200", "dataMessage": {"message": ""}}}
        )
        is None
    )


def test_signal_ignores_self_message() -> None:
    adapter = SignalAdapter(
        account="+100",
        base_url="http://signal.local",
        allowed_senders={"+100"},
    )

    assert (
        adapter.parse_event(
            {
                "envelope": {
                    "sourceNumber": "+100",
                    "dataMessage": {"message": "from me"},
                }
            }
        )
        is None
    )


def test_signal_parse_note_to_self_sync_message() -> None:
    adapter = SignalAdapter(
        account="+100",
        base_url="http://signal.local",
        allowed_senders={"+100"},
    )
    event = {
        "envelope": {
            "sourceNumber": "+100",
            "syncMessage": {
                "sentMessage": {
                    "destinationNumber": "+100",
                    "message": "hello openpoke",
                    "timestamp": 123,
                }
            },
        }
    }

    assert adapter.parse_event(event) == InboundMessage(
        source="signal",
        sender="+100",
        text="hello openpoke",
        raw=event,
    )


def test_signal_note_to_self_requires_allowlist() -> None:
    adapter = SignalAdapter(
        account="+100",
        base_url="http://signal.local",
        allowed_senders={"+200"},
    )

    assert (
        adapter.parse_event(
            {
                "envelope": {
                    "sourceNumber": "+100",
                    "syncMessage": {
                        "sentMessage": {
                            "destinationNumber": "+100",
                            "message": "blocked",
                        }
                    },
                }
            }
        )
        is None
    )


def test_signal_note_to_self_requires_self_destination() -> None:
    adapter = SignalAdapter(
        account="+100",
        base_url="http://signal.local",
        allowed_senders={"+100"},
    )

    assert (
        adapter.parse_event(
            {
                "envelope": {
                    "sourceNumber": "+100",
                    "syncMessage": {
                        "sentMessage": {
                            "destinationNumber": "+200",
                            "message": "not note to self",
                        }
                    },
                }
            }
        )
        is None
    )


def test_signal_note_to_self_ignores_empty_message() -> None:
    adapter = SignalAdapter(
        account="+100",
        base_url="http://signal.local",
        allowed_senders={"+100"},
    )

    assert (
        adapter.parse_event(
            {
                "envelope": {
                    "sourceNumber": "+100",
                    "syncMessage": {
                        "sentMessage": {
                            "destinationNumber": "+100",
                            "message": "",
                        }
                    },
                }
            }
        )
        is None
    )


def test_signal_rejects_sender_not_in_allowlist() -> None:
    adapter = SignalAdapter(
        account="+100",
        base_url="http://signal.local",
        allowed_senders={"+200"},
    )

    assert (
        adapter.parse_event(
            {
                "envelope": {
                    "sourceNumber": "+300",
                    "dataMessage": {"message": "blocked"},
                }
            }
        )
        is None
    )


def test_signal_denies_all_without_allowlist() -> None:
    adapter = SignalAdapter(
        account="+100",
        base_url="http://signal.local",
        allowed_senders=set(),
    )

    assert (
        adapter.parse_event(
            {
                "envelope": {
                    "sourceNumber": "+200",
                    "dataMessage": {"message": "blocked"},
                }
            }
        )
        is None
    )


def test_signal_send_uses_json_rpc_send() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"result": {"timestamp": 1}})

    async def run_test() -> None:
        async with _client(handler) as client:
            adapter = SignalAdapter(
                account="+100",
                base_url="http://signal.local",
                allowed_senders={"+200"},
                client=client,
            )

            await adapter.send(ReplyTarget(source="signal", destination="+200"), "hi")

    asyncio.run(run_test())

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/api/v1/rpc"
    assert json.loads(requests[0].content) == {
        "jsonrpc": "2.0",
        "method": "send",
        "params": {
            "account": "+100",
            "message": "hi",
            "recipient": ["+200"],
        },
        "id": 1,
    }


def test_signal_ignores_note_to_self_echo_after_send() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"timestamp": 123}})

    async def run_test() -> SignalAdapter:
        async with _client(handler) as client:
            adapter = SignalAdapter(
                account="+100",
                base_url="http://signal.local",
                allowed_senders={"+100"},
                client=client,
            )

            await adapter.send(ReplyTarget(source="signal", destination="+100"), "hi")
            return adapter

    adapter = asyncio.run(run_test())

    assert (
        adapter.parse_event(
            {
                "envelope": {
                    "sourceNumber": "+100",
                    "syncMessage": {
                        "sentMessage": {
                            "destinationNumber": "+100",
                            "message": "hi",
                            "timestamp": 123,
                        }
                    },
                }
            }
        )
        is None
    )


def test_signal_sse_event_dispatches_inbound_message() -> None:
    received: list[InboundMessage] = []
    adapter = SignalAdapter(
        account="+100",
        base_url="http://signal.local",
        allowed_senders={"+200"},
        on_message=received.append,
    )

    adapter.dispatch_event(
        json.dumps(
            {
                "envelope": {
                    "sourceNumber": "+200",
                    "dataMessage": {"message": "hello"},
                }
            }
        )
    )

    assert received == [
        InboundMessage(
            source="signal",
            sender="+200",
            text="hello",
            raw={
                "envelope": {
                    "sourceNumber": "+200",
                    "dataMessage": {"message": "hello"},
                }
            },
        )
    ]
