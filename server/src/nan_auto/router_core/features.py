"""Deterministic request features for the router (NAU-04, spec 3 and 6.1).

Vision, audio, tools, output format and token size are facts of the request,
not questions for Clef: code answers what it can read off the body. The
extractor is a pure function of the parsed body plus the optional
`x-conversation-id` header value — no network, no I/O, no message text stored
or logged; it returns booleans, counts and a hash, nothing else.
"""

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from pydantic import ValidationError

from nan_auto.clef_client.questions import estimate_tokens
from nan_auto.schema.chat import ChatRequest, Message

# Joined with a separator that cannot appear inside a message, so the system
# prompt and the first user message hash to different keys when swapped.
_CONVERSATION_SEPARATOR = "\x1e"
# Cheap floor for a non-textual part (a low-detail image is ~85 tokens); the
# exact cost depends on the model and is not worth the latency.
_NON_TEXT_PART_TOKENS = 85
_CODE_FENCE = "```"
_CONVERSATION_KEY_CHARS = 32


class FeatureExtractionError(ValueError):
    """The body does not have the minimum shape chat/completions needs.

    Raised instead of letting `AttributeError` or `KeyError` escape, and its
    message names field paths only, so a bad request never echoes its text.
    """


@dataclass(frozen=True)
class RequestFeatures:
    """Observable facts of a chat request; no field may ever hold message text."""

    has_image: bool = False
    has_audio: bool = False
    est_tokens: int = 0
    has_tools: bool = False
    tool_choice: str | dict[str, Any] | None = None
    response_format: str = "text"
    stream: bool = False
    turns: int = 0
    has_code_fences: bool = False
    conversation_key: str = ""


def _parse(body: Any) -> ChatRequest:
    """Parse a raw body, mapping any shape failure to an explicit error."""
    if isinstance(body, ChatRequest):
        return body
    try:
        return ChatRequest.model_validate(body)
    except ValidationError as error:
        paths = ", ".join(
            str(location) for location in [tuple(item["loc"]) for item in error.errors()]
        )
        raise FeatureExtractionError(
            f"invalid chat/completions body at: {paths or 'unknown'}"
        ) from error


def _scan_content(
    message: Message, *, has_image: bool, has_audio: bool
) -> tuple[str, bool, bool, int]:
    """Return the text of one message, updated image/audio facts and non-text part count."""
    value = message.content
    if value is None:
        return "", has_image, has_audio, 0
    if isinstance(value, str):
        return value, has_image, has_audio, 0

    texts: list[str] = []
    non_text_parts = 0
    for part in value:
        if isinstance(part, str):
            texts.append(part)
            continue
        part_type = part.type or "text"
        if part_type == "image_url":
            has_image = True
            non_text_parts += 1
        elif part_type == "input_audio":
            has_audio = True
            non_text_parts += 1
        # Unknown part types fall through as text: their `text`, when present,
        # is still counted so nothing is silently dropped.
        if part.text is not None:
            texts.append(part.text)
    return "".join(texts), has_image, has_audio, non_text_parts


def _first_text_of_role(messages: list[Message], role: str) -> str:
    """Text of the first message with the given role; empty when there is none."""
    for message in messages:
        if message.role == role:
            text, _, _, _ = _scan_content(message, has_image=False, has_audio=False)
            return text
    return ""


def _hashed_conversation_key(request: ChatRequest) -> str:
    """Stable key from the system prompt plus the first user message.

    Only the digest leaves this module: the hash cannot be reversed into the
    prompt, and no output field ever carries text.
    """
    system = _first_text_of_role(request.messages, "system")
    user = _first_text_of_role(request.messages, "user")
    seed = system + _CONVERSATION_SEPARATOR + user
    return sha256(seed.encode("utf-8")).hexdigest()[:_CONVERSATION_KEY_CHARS]


def extract_features(
    body: Any,
    *,
    conversation_id: str | None = None,
) -> RequestFeatures:
    """Extract the deterministic facts of one chat request.

    `conversation_id` is the `x-conversation-id` header value when the client
    sent one; when absent the key is a stable hash of the system prompt plus
    the first user message.
    """
    request = _parse(body)
    has_image = False
    has_audio = False
    est_tokens = 0
    has_code_fences = False
    for message in request.messages:
        text, has_image, has_audio, non_text_parts = _scan_content(
            message, has_image=has_image, has_audio=has_audio
        )
        est_tokens += estimate_tokens(text) + _NON_TEXT_PART_TOKENS * non_text_parts
        if _CODE_FENCE in text:
            has_code_fences = True
    header = conversation_id.strip() if conversation_id else ""
    return RequestFeatures(
        has_image=has_image,
        has_audio=has_audio,
        est_tokens=est_tokens,
        has_tools=bool(request.tools),
        tool_choice=request.tool_choice,
        response_format=(request.response_format.type or "text")
        if request.response_format
        else "text",
        stream=request.stream,
        turns=len(request.messages),
        has_code_fences=has_code_fences,
        conversation_key=header if header else _hashed_conversation_key(request),
    )
