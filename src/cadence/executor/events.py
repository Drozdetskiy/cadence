from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TextContent:
    text: str = ""


@dataclass
class ToolUseBlock:
    name: str = ""


ContentItem = TextContent | ToolUseBlock


@dataclass
class AssistantMessage:
    content: list[ContentItem] = field(default_factory=list)


@dataclass
class AssistantEvent:
    type: str
    message: AssistantMessage | None = None


@dataclass
class TextDelta:
    text: str = ""


@dataclass
class ContentBlockDeltaEvent:
    delta: TextDelta | None = None


@dataclass
class ContentBlockStartEvent:
    content_block: ContentItem | None = None


@dataclass
class ResultPayload:
    output: str = ""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass
class ResultEvent:
    result: ResultPayload | str | None = None
    usage: Usage | None = None
    session_id: str = ""
    model: str = ""


ClaudeEvent = AssistantEvent | ContentBlockDeltaEvent | ContentBlockStartEvent | ResultEvent


def _parse_content_item(d: dict[str, object]) -> ContentItem | None:
    t = d.get("type")
    if t == "text":
        text = d.get("text")
        if isinstance(text, str):
            return TextContent(text=text)
        return None
    if t == "tool_use":
        name = d.get("name")
        if isinstance(name, str):
            return ToolUseBlock(name=name)
        return None
    return None


def _parse_assistant(raw: dict[str, object], etype: str) -> AssistantEvent:
    msg_raw = raw.get("message")
    if not isinstance(msg_raw, dict):
        return AssistantEvent(type=etype, message=None)
    content_raw = msg_raw.get("content")
    items: list[ContentItem] = []
    if isinstance(content_raw, list):
        for item in content_raw:
            if isinstance(item, dict):
                parsed = _parse_content_item(item)
                if parsed is not None:
                    items.append(parsed)
    return AssistantEvent(type=etype, message=AssistantMessage(content=items))


def _parse_content_block_delta(raw: dict[str, object]) -> ContentBlockDeltaEvent:
    delta_raw = raw.get("delta")
    if isinstance(delta_raw, dict) and delta_raw.get("type") == "text_delta":
        txt = delta_raw.get("text")
        if isinstance(txt, str):
            return ContentBlockDeltaEvent(delta=TextDelta(text=txt))
    return ContentBlockDeltaEvent(delta=None)


def _parse_content_block_start(raw: dict[str, object]) -> ContentBlockStartEvent:
    cb_raw = raw.get("content_block")
    cb = _parse_content_item(cb_raw) if isinstance(cb_raw, dict) else None
    return ContentBlockStartEvent(content_block=cb)


def _coerce_int(v: object) -> int:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v)
        except ValueError:
            return 0
    return 0


def _parse_usage(raw: object) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    return Usage(
        input_tokens=_coerce_int(raw.get("input_tokens", 0)),
        output_tokens=_coerce_int(raw.get("output_tokens", 0)),
        cache_read_tokens=_coerce_int(raw.get("cache_read_input_tokens", 0)),
        cache_creation_tokens=_coerce_int(raw.get("cache_creation_input_tokens", 0)),
    )


def _parse_result(raw: dict[str, object]) -> ResultEvent:
    result_raw = raw.get("result")
    usage = _parse_usage(raw.get("usage"))
    session_raw = raw.get("session_id")
    session_id = session_raw if isinstance(session_raw, str) else ""
    model_raw = raw.get("model")
    model = model_raw if isinstance(model_raw, str) else ""
    if isinstance(result_raw, str):
        return ResultEvent(result=result_raw, usage=usage, session_id=session_id, model=model)
    if isinstance(result_raw, dict):
        out = result_raw.get("output")
        return ResultEvent(
            result=ResultPayload(output=out if isinstance(out, str) else ""),
            usage=usage,
            session_id=session_id,
            model=model,
        )
    return ResultEvent(result=None, usage=usage, session_id=session_id, model=model)


def parse_event(raw: object) -> ClaudeEvent | None:
    if not isinstance(raw, dict):
        return None
    etype = raw.get("type")
    if etype in ("assistant", "message_stop") and isinstance(etype, str):
        return _parse_assistant(raw, etype)
    if etype == "content_block_delta":
        return _parse_content_block_delta(raw)
    if etype == "content_block_start":
        return _parse_content_block_start(raw)
    if etype == "result":
        return _parse_result(raw)
    return None
