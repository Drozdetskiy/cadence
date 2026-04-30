from __future__ import annotations

from cadence.executor.events import (
    AssistantEvent,
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ResultEvent,
    ResultPayload,
    TextContent,
    TextDelta,
    ToolUseBlock,
    parse_event,
)


class TestParseEvent:
    def test_returns_none_for_non_dict(self) -> None:
        assert parse_event("not a dict") is None
        assert parse_event(None) is None
        assert parse_event(42) is None
        assert parse_event([1, 2]) is None

    def test_returns_none_for_unknown_type(self) -> None:
        assert parse_event({"type": "unknown_event_type"}) is None

    def test_returns_none_for_missing_type(self) -> None:
        assert parse_event({"foo": "bar"}) is None

    def test_assistant_with_text_content(self) -> None:
        ev = parse_event({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "text", "text": " world"},
                ],
            },
        })
        assert isinstance(ev, AssistantEvent)
        assert ev.type == "assistant"
        assert ev.message is not None
        assert ev.message.content == [TextContent(text="hello"), TextContent(text=" world")]

    def test_assistant_with_tool_use_content(self) -> None:
        ev = parse_event({
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Read"}]},
        })
        assert isinstance(ev, AssistantEvent)
        assert ev.message is not None
        assert ev.message.content == [ToolUseBlock(name="Read")]

    def test_assistant_drops_unknown_content_items(self) -> None:
        ev = parse_event({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "ok"},
                    {"type": "weird"},
                    "not a dict",
                ],
            },
        })
        assert isinstance(ev, AssistantEvent)
        assert ev.message is not None
        assert ev.message.content == [TextContent(text="ok")]

    def test_assistant_with_missing_message(self) -> None:
        ev = parse_event({"type": "assistant"})
        assert isinstance(ev, AssistantEvent)
        assert ev.message is None

    def test_assistant_with_non_dict_message(self) -> None:
        ev = parse_event({"type": "assistant", "message": "string"})
        assert isinstance(ev, AssistantEvent)
        assert ev.message is None

    def test_message_stop_routed_to_assistant_event(self) -> None:
        ev = parse_event({
            "type": "message_stop",
            "message": {"content": [{"type": "text", "text": "done"}]},
        })
        assert isinstance(ev, AssistantEvent)
        assert ev.type == "message_stop"
        assert ev.message is not None
        assert ev.message.content == [TextContent(text="done")]

    def test_text_content_without_text_field_dropped(self) -> None:
        ev = parse_event({
            "type": "assistant",
            "message": {"content": [{"type": "text"}]},
        })
        assert isinstance(ev, AssistantEvent)
        assert ev.message is not None
        assert ev.message.content == []

    def test_tool_use_without_name_dropped(self) -> None:
        ev = parse_event({
            "type": "assistant",
            "message": {"content": [{"type": "tool_use"}]},
        })
        assert isinstance(ev, AssistantEvent)
        assert ev.message is not None
        assert ev.message.content == []

    def test_content_block_delta_text(self) -> None:
        ev = parse_event({
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "chunk"},
        })
        assert isinstance(ev, ContentBlockDeltaEvent)
        assert ev.delta == TextDelta(text="chunk")

    def test_content_block_delta_unknown_type(self) -> None:
        ev = parse_event({
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta"},
        })
        assert isinstance(ev, ContentBlockDeltaEvent)
        assert ev.delta is None

    def test_content_block_delta_missing_text(self) -> None:
        ev = parse_event({
            "type": "content_block_delta",
            "delta": {"type": "text_delta"},
        })
        assert isinstance(ev, ContentBlockDeltaEvent)
        assert ev.delta is None

    def test_content_block_start_tool_use(self) -> None:
        ev = parse_event({
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Bash"},
        })
        assert isinstance(ev, ContentBlockStartEvent)
        assert ev.content_block == ToolUseBlock(name="Bash")

    def test_content_block_start_text(self) -> None:
        ev = parse_event({
            "type": "content_block_start",
            "content_block": {"type": "text", "text": ""},
        })
        assert isinstance(ev, ContentBlockStartEvent)
        assert ev.content_block == TextContent(text="")

    def test_content_block_start_unknown(self) -> None:
        ev = parse_event({
            "type": "content_block_start",
            "content_block": {"type": "thinking"},
        })
        assert isinstance(ev, ContentBlockStartEvent)
        assert ev.content_block is None

    def test_result_with_dict(self) -> None:
        ev = parse_event({"type": "result", "result": {"output": "ok"}})
        assert isinstance(ev, ResultEvent)
        assert ev.result == ResultPayload(output="ok")

    def test_result_with_dict_missing_output(self) -> None:
        ev = parse_event({"type": "result", "result": {}})
        assert isinstance(ev, ResultEvent)
        assert ev.result == ResultPayload(output="")

    def test_result_with_string(self) -> None:
        ev = parse_event({"type": "result", "result": "stringy"})
        assert isinstance(ev, ResultEvent)
        assert ev.result == "stringy"

    def test_result_with_none(self) -> None:
        ev = parse_event({"type": "result"})
        assert isinstance(ev, ResultEvent)
        assert ev.result is None
