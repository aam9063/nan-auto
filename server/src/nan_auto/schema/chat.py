"""Pydantic models for the OpenAI-compatible chat/completions body (NAU-04).

Tolerant by design: every model allows extra fields because real clients
(Cursor, Cline, the openai SDK) send fields the spec never mentions, and a
router that rejects them is a broken proxy. These models are only the parsing
boundary for the deterministic feature extractor; facts leave as booleans and
numbers, never as text (spec 6.1).
"""

from typing import Any

from pydantic import BaseModel, ConfigDict


class ContentPart(BaseModel):
    """One content part; unknown `type`s are kept and read as text."""

    model_config = ConfigDict(extra="allow")

    type: str | None = None
    text: str | None = None
    image_url: dict[str, Any] | str | None = None
    input_audio: dict[str, Any] | None = None


class Message(BaseModel):
    """One chat message with string or multipart content."""

    model_config = ConfigDict(extra="allow")

    role: str
    content: str | list[str | ContentPart] | None = None
    name: str | None = None


class ResponseFormat(BaseModel):
    """`response_format` value; only `type` is a deterministic fact."""

    model_config = ConfigDict(extra="allow")

    type: str | None = None


class Tool(BaseModel):
    """One tool definition; the extractor only needs to know one exists."""

    model_config = ConfigDict(extra="allow")

    type: str | None = None
    function: dict[str, Any] | None = None


class ChatRequest(BaseModel):
    """The minimum body of `POST /v1/chat/completions`, everything else allowed."""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[Message]
    stream: bool = False
    tools: list[Tool] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: ResponseFormat | None = None
