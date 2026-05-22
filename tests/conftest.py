from __future__ import annotations

import pytest

from server.messaging.types import InboundMessage, ReplyTarget


@pytest.fixture
def signal_target() -> ReplyTarget:
    return ReplyTarget(source="signal", destination="+15551234567")


@pytest.fixture
def signal_message() -> InboundMessage:
    return InboundMessage(
        source="signal",
        sender="+15551234567",
        text="Hello from Signal",
    )
