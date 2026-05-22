from __future__ import annotations

import pytest

from server.messaging.types import InboundMessage, ReplyTarget


class TestReplyTarget:
    def test_requires_source(self) -> None:
        with pytest.raises(ValueError, match="source is required"):
            ReplyTarget(source="", destination="+111")

    def test_requires_destination(self) -> None:
        with pytest.raises(ValueError, match="destination is required"):
            ReplyTarget(source="signal", destination="")

    def test_valid_target(self) -> None:
        target = ReplyTarget(source="signal", destination="+15551234567")
        assert target.source == "signal"
        assert target.destination == "+15551234567"

    def test_is_frozen(self) -> None:
        target = ReplyTarget(source="signal", destination="+111")
        with pytest.raises(AttributeError):
            target.source = "telegram"  # type: ignore[misc]


class TestInboundMessage:
    def test_requires_source(self) -> None:
        with pytest.raises(ValueError, match="source is required"):
            InboundMessage(source="", sender="+111", text="hi")

    def test_requires_text(self) -> None:
        with pytest.raises(ValueError, match="text is required"):
            InboundMessage(source="signal", sender="+111", text="")

    def test_valid_message(self) -> None:
        msg = InboundMessage(
            source="signal",
            sender="+15551234567",
            text="Hello from Signal",
        )
        assert msg.source == "signal"
        assert msg.sender == "+15551234567"
        assert msg.text == "Hello from Signal"
        assert msg.raw is None

    def test_with_raw_payload(self) -> None:
        raw = {"envelope": {"sourceNumber": "+111"}}
        msg = InboundMessage(
            source="signal",
            sender="+111",
            text="hi",
            raw=raw,
        )
        assert msg.raw == raw

    def test_is_frozen(self) -> None:
        msg = InboundMessage(source="signal", sender="+111", text="hi")
        with pytest.raises(AttributeError):
            msg.text = "changed"  # type: ignore[misc]
