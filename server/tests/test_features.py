"""Behavior tests for the deterministic feature extractor (NAU-04, spec 6.1).

The extractor is a pure function of the body plus the optional
`x-conversation-id` header value; it never stores, logs or echoes message
text, so no assertion below depends on content reaching an output field.
"""

import pytest

from nan_auto.router_core.features import FeatureExtractionError, extract_features
from nan_auto.schema.chat import ChatRequest


def cursor_like_body() -> dict:
    """A real-shaped Cursor request with fields the spec never mentions."""
    return {
        "model": "nan-auto",
        "messages": [
            {"role": "system", "content": "Eres un asistente de codigo."},
            {"role": "user", "content": [{"type": "text", "text": "hola"}]},
        ],
        "stream": False,
        "temperature": 0.7,
        "top_p": 1,
        "presence_penalty": 0.3,
        "frequency_penalty": 0.2,
        "cursor_settings": {"includeDiff": True, "applyRepoState": "session"},
        "unknown_future_field": {"nested": [1, 2, 3]},
        "metadata": {"trace": "abc"},
    }


class TestT1Models:
    def test_unknown_top_level_fields_validate(self) -> None:
        request = ChatRequest.model_validate(cursor_like_body())

        assert request.model == "nan-auto"
        assert len(request.messages) == 2
        # Extra fields survive, they are not rejected and not silently dropped.
        assert request.model_extra is not None
        assert request.model_extra["cursor_settings"] == {
            "includeDiff": True,
            "applyRepoState": "session",
        }

    def test_unknown_part_types_validate(self) -> None:
        body = {
            "model": "nan-auto",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "mira"},
                        {"type": "some_new_part_type", "payload": {"x": 1}, "text": "extra"},
                    ],
                }
            ],
            "custom_client_flag": True,
        }

        request = ChatRequest.model_validate(body)

        parts = request.messages[0].content
        assert isinstance(parts, list)
        second = parts[1]
        assert not isinstance(second, str)
        assert second.type == "some_new_part_type"
        assert second.text == "extra"

    def test_extract_features_accepts_cursor_like_body(self) -> None:
        features = extract_features(cursor_like_body())

        assert features.stream is False

    def test_missing_messages_is_explicit_error(self) -> None:
        with pytest.raises(FeatureExtractionError) as excinfo:
            extract_features({"model": "nan-auto"})
        assert "messages" in str(excinfo.value)

    def test_body_of_wrong_type_is_explicit_error(self) -> None:
        with pytest.raises(FeatureExtractionError):
            extract_features(["not", "a", "dict"])
        with pytest.raises(FeatureExtractionError):
            extract_features(None)  # type: ignore[arg-type]

    def test_error_message_does_not_leak_text(self) -> None:
        with pytest.raises(FeatureExtractionError) as excinfo:
            extract_features(
                {
                    "model": "nan-auto",
                    "messages": [{"role": "user", "content": {"secreto": "texto-privado"}}],
                }
            )

        assert "texto-privado" not in str(excinfo.value)


def _with_content(content: object) -> dict:
    return {"model": "nan-auto", "messages": [{"role": "user", "content": content}]}


class TestT2HasImageHasAudio:
    def test_image_part_in_list_content_marks_image(self) -> None:
        body = _with_content(
            [
                {"type": "text", "text": "mira esto"},
                {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
            ]
        )

        features = extract_features(body)

        assert features.has_image is True
        assert features.has_audio is False

    def test_audio_part_marks_audio(self) -> None:
        body = _with_content(
            [
                {"type": "text", "text": "escucha"},
                {"type": "input_audio", "input_audio": {"data": "base64", "format": "wav"}},
            ]
        )

        features = extract_features(body)

        assert features.has_audio is True
        assert features.has_image is False

    def test_both_parts_mark_both_facts(self) -> None:
        body = _with_content(
            [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}},
                {"type": "input_audio", "input_audio": {"data": "base64", "format": "mp3"}},
            ]
        )

        features = extract_features(body)

        assert features.has_image is True
        assert features.has_audio is True

    def test_plain_string_content_has_neither(self) -> None:
        features = extract_features(_with_content("solo texto"))

        assert features.has_image is False
        assert features.has_audio is False

    def test_multimodal_shape_across_messages(self) -> None:
        body = {
            "model": "nan-auto",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,a"}}
                    ],
                },
                {"role": "assistant", "content": "que imagen?"},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_audio", "input_audio": {"data": "b64", "format": "wav"}}
                    ],
                },
            ],
        }

        features = extract_features(body)

        assert features.has_image is True
        assert features.has_audio is True

    def test_unknown_part_type_is_not_image_nor_audio(self) -> None:
        body = _with_content([{"type": "some_new_part_type", "payload": {}}])

        features = extract_features(body)

        assert features.has_image is False
        assert features.has_audio is False


class TestT3EstTokens:
    def test_four_thousand_chars_estimate_about_a_thousand_tokens(self) -> None:
        features = extract_features(_with_content("x" * 4000))

        assert features.est_tokens == 1000

    def test_counts_across_all_messages(self) -> None:
        body = {
            "model": "nan-auto",
            "messages": [
                {"role": "system", "content": "a" * 400},
                {"role": "user", "content": "b" * 400},
                {"role": "assistant", "content": "c" * 400},
            ],
        }

        features = extract_features(body)

        # 1200 chars / 4 chars per token = 300, same estimate the project
        # already uses in clef_client.questions.
        assert features.est_tokens == 300

    def test_non_textual_parts_add_their_own_cost(self) -> None:
        body = _with_content(
            [
                {"type": "text", "text": "m" * 400},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}},
                {"type": "input_audio", "input_audio": {"data": "base64", "format": "wav"}},
            ]
        )

        features = extract_features(body)

        # 100 tokens of text plus two non-textual parts at the fixed floor.
        assert features.est_tokens == 100 + 2 * 85

    def test_multipart_text_is_counted_too(self) -> None:
        body = _with_content(
            [
                {"type": "text", "text": "a" * 200},
                {"type": "text", "text": "b" * 200},
            ]
        )

        features = extract_features(body)

        assert features.est_tokens == 100

    def test_null_content_counts_zero(self) -> None:
        body = {
            "model": "nan-auto",
            "messages": [
                {"role": "assistant", "content": None, "tool_calls": [{"id": "t1"}]},
                {"role": "user", "content": "hola"},
            ],
        }

        features = extract_features(body)

        assert features.est_tokens == 1


class TestT4ToolsFormatStream:
    def test_tools_and_tool_choice_reflected(self) -> None:
        body = {
            "model": "nan-auto",
            "messages": [{"role": "user", "content": "tiempo en Madrid"}],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "get_weather", "parameters": {"q": "str"}},
                },
                {"type": "function", "function": {"name": "get_time"}},
            ],
            "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
        }

        features = extract_features(body)

        assert features.has_tools is True
        assert features.tool_choice == {"type": "function", "function": {"name": "get_weather"}}

    def test_tool_choice_string_reflected(self) -> None:
        body = {
            "model": "nan-auto",
            "messages": [{"role": "user", "content": "hola"}],
            "tools": [{"type": "function", "function": {"name": "get_time"}}],
            "tool_choice": "auto",
        }

        features = extract_features(body)

        assert features.has_tools is True
        assert features.tool_choice == "auto"

    def test_no_tools_means_no_tools(self) -> None:
        body = {"model": "nan-auto", "messages": [{"role": "user", "content": "hola"}]}

        features = extract_features(body)

        assert features.has_tools is False
        assert features.tool_choice is None

    def test_empty_tools_list_counts_as_no_tools(self) -> None:
        body = {"model": "nan-auto", "messages": [{"role": "user", "content": "hola"}], "tools": []}

        features = extract_features(body)

        assert features.has_tools is False

    def test_response_format_json_schema_without_format_is_text(self) -> None:
        with_format = {
            "model": "nan-auto",
            "messages": [{"role": "user", "content": "hola"}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "r", "strict": True},
            },
        }
        without_format = {"model": "nan-auto", "messages": [{"role": "user", "content": "hola"}]}

        assert extract_features(with_format).response_format == "json_schema"
        assert extract_features(without_format).response_format == "text"

    def test_response_format_text_and_json_object(self) -> None:
        for expected in ("text", "json_object"):
            body = {
                "model": "nan-auto",
                "messages": [{"role": "user", "content": "hola"}],
                "response_format": {"type": expected},
            }

            assert extract_features(body).response_format == expected

    def test_stream_defaults_to_false_and_is_reflected(self) -> None:
        base = {"model": "nan-auto", "messages": [{"role": "user", "content": "hola"}]}

        assert extract_features(base).stream is False
        assert extract_features({**base, "stream": True}).stream is True


class TestT5TurnsAndFences:
    def test_three_messages_are_three_turns(self) -> None:
        body = {
            "model": "nan-auto",
            "messages": [
                {"role": "system", "content": "seras util"},
                {"role": "user", "content": "hola"},
                {"role": "assistant", "content": "que tal"},
            ],
        }

        features = extract_features(body)

        assert features.turns == 3

    def test_code_fence_is_detected(self) -> None:
        body = _with_content("testo\n```python\nprint(1)\n```\nfin")

        features = extract_features(body)

        assert features.has_code_fences is True

    def test_no_fence_is_not_detected(self) -> None:
        body = _with_content("testo normal sin cercas")

        features = extract_features(body)

        assert features.has_code_fences is False

    def test_fence_inside_multipart_is_detected(self) -> None:
        body = _with_content(
            [
                {"type": "text", "text": "antes"},
                {"type": "text", "text": "```\ncodigo\n```"},
            ]
        )

        features = extract_features(body)

        assert features.has_code_fences is True


class TestT6ConversationKey:
    def test_header_value_wins_when_present(self) -> None:
        body = {
            "model": "nan-auto",
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "primero"},
            ],
        }

        features = extract_features(body, conversation_id="conv-42")

        assert features.conversation_key == "conv-42"

    def test_empty_header_falls_back_to_hash(self) -> None:
        body = {
            "model": "nan-auto",
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "primero"},
            ],
        }

        with_header = extract_features(body, conversation_id="conv-42").conversation_key
        blank = extract_features(body, conversation_id="   ").conversation_key
        hashed = extract_features(body).conversation_key

        assert blank == hashed
        assert blank != with_header

    def test_hash_is_stable_for_the_same_conversation(self) -> None:
        first = {
            "model": "nan-auto",
            "messages": [
                {"role": "system", "content": "Eres un asistente."},
                {"role": "user", "content": "cual es la capital?"},
            ],
        }
        # A retry of the same conversation: same system + same first user
        # message, different trailing turns.
        retry = {
            **first,
            "messages": [
                *first["messages"],
                {"role": "assistant", "content": "Madrid"},
                {"role": "user", "content": "gracias"},
            ],
        }

        assert extract_features(first).conversation_key == extract_features(retry).conversation_key

    def test_different_conversations_do_not_collide(self) -> None:
        def body(prompt: str, system: str = "Eres un asistente.") -> dict:
            return {
                "model": "nan-auto",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            }

        keys = {
            extract_features(body("hola")).conversation_key,
            extract_features(body("adios")).conversation_key,
            extract_features(body("hola", system="Eres un poeta.")).conversation_key,
        }

        assert len(keys) == 3

    def test_key_is_a_hash_not_the_prompt(self) -> None:
        features = extract_features(
            {
                "model": "nan-auto",
                "messages": [
                    {"role": "system", "content": "CLAVE-SECRETA-DE-SISTEMA"},
                    {"role": "user", "content": "MENSAJE-PRIVADO-DEL-USUARIO"},
                ],
            }
        )
        key = features.conversation_key

        assert set(key) <= set("0123456789abcdef")
        assert "CLAVE-SECRETA-DE-SISTEMA" not in key
        assert "MENSAJE-PRIVADO-DEL-USUARIO" not in key

    def test_no_system_nor_user_message_still_gives_a_key(self) -> None:
        features = extract_features(
            {"model": "nan-auto", "messages": [{"role": "user", "content": []}]}
        )

        assert len(features.conversation_key) == 32
