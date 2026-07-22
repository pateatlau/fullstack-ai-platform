import asyncio
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, cast

from google import genai
from google.genai import types

from app.providers.base import (
    ChatMessageInput,
    ProviderChunk,
    ProviderCompletion,
    ProviderToolCall,
    ProviderToolCompletion,
    ProviderUsage,
)
from app.schemas.chat import ChatMessageSchema


def _usage_from_response(response: Any) -> ProviderUsage | None:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return None
    return ProviderUsage(
        prompt_tokens=getattr(meta, "prompt_token_count", None),
        completion_tokens=getattr(meta, "candidates_token_count", None),
        total_tokens=getattr(meta, "total_token_count", None),
    )


def _message_to_line(message: ChatMessageSchema) -> str:
    return f"{message.role}: {message.content}"


def _messages_to_prompt(messages: list[ChatMessageSchema]) -> str:
    return "\n".join(_message_to_line(message) for message in messages)


def _parse_tool_arguments(raw: str | dict[str, object] | None) -> dict[str, object]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return cast(dict[str, object], raw)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return cast(dict[str, object], parsed)
    return {}


def _to_gemini_contents(
    messages: list[ChatMessageInput],
) -> tuple[str | None, list[Any]]:
    system_parts: list[str] = []
    contents: list[Any] = []
    tool_call_names_by_id: dict[str, str] = {}

    for message in messages:
        if isinstance(message, ChatMessageSchema):
            if message.role == "system":
                system_parts.append(message.content)
                continue
            role = "user" if message.role == "user" else "model"
            contents.append(
                types.Content(role=role, parts=[types.Part(text=message.content)])
            )
            continue

        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            parts: list[Any] = []
            text_content = message.get("content")
            if isinstance(text_content, str) and text_content:
                parts.append(types.Part(text=text_content))
            for call in message.get("tool_calls", []):
                if not isinstance(call, dict) or call.get("type") != "function":
                    continue
                function = call.get("function")
                if not isinstance(function, dict):
                    continue
                raw_args = function.get("arguments")
                args = _parse_tool_arguments(
                    raw_args if isinstance(raw_args, (str, dict)) else None
                )
                call_id = str(call.get("id") or f"call_{uuid.uuid4().hex[:12]}")
                call_name = str(function.get("name", ""))
                tool_call_names_by_id[call_id] = call_name
                thought_signature = call.get("thought_signature")
                function_call = types.FunctionCall(
                    id=call_id,
                    name=call_name,
                    args=args,
                )
                part_kwargs: dict[str, Any] = {"function_call": function_call}
                if isinstance(thought_signature, bytes):
                    part_kwargs["thought_signature"] = thought_signature
                parts.append(types.Part(**part_kwargs))
            contents.append(types.Content(role="model", parts=parts))
            continue

        if role == "tool":
            tool_call_id = str(message.get("tool_call_id", ""))
            function_name = tool_call_names_by_id.get(tool_call_id, "")
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=tool_call_id,
                                name=function_name,
                                response={"output": message.get("content", "")},
                            )
                        )
                    ],
                )
            )
            continue

        if role in {"user", "assistant"}:
            gemini_role = "user" if role == "user" else "model"
            contents.append(
                types.Content(
                    role=gemini_role,
                    parts=[types.Part(text=str(message.get("content", "")))],
                )
            )

    system = "\n\n".join(system_parts) if system_parts else None
    return system, contents


def _to_gemini_tools(tools: list[dict[str, object]]) -> list[Any]:
    declarations: list[Any] = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        declarations.append(
            types.FunctionDeclaration(
                name=str(function.get("name", "")),
                description=str(function.get("description", "")),
                parameters=function.get("parameters", {"type": "object"}),
            )
        )
    if not declarations:
        return []
    return [types.Tool(function_declarations=declarations)]


def _extract_tool_completion(response: Any) -> ProviderToolCompletion:
    tool_calls: list[ProviderToolCall] = []
    text_parts: list[str] = []
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return ProviderToolCompletion(
            content=None,
            tool_calls=[],
            usage=_usage_from_response(response),
        )

    parts = getattr(candidates[0].content, "parts", []) or []
    for part in parts:
        function_call = getattr(part, "function_call", None)
        if function_call is not None:
            raw_args = getattr(function_call, "args", None)
            arguments = raw_args if isinstance(raw_args, dict) else {}
            thought_signature = getattr(part, "thought_signature", None)
            tool_calls.append(
                ProviderToolCall(
                    id=getattr(function_call, "id", None)
                    or f"call_{uuid.uuid4().hex[:12]}",
                    name=getattr(function_call, "name", "") or "",
                    arguments=cast(dict[str, object], arguments),
                    thought_signature=(
                        thought_signature
                        if isinstance(thought_signature, bytes)
                        else None
                    ),
                )
            )
            continue

        part_text = getattr(part, "text", None)
        if isinstance(part_text, str) and part_text:
            text_parts.append(part_text)

    finish_reason = None
    if candidates:
        finish_reason = getattr(candidates[0], "finish_reason", None)

    content = "".join(text_parts) or None
    return ProviderToolCompletion(
        content=content,
        tool_calls=tool_calls,
        finish_reason=str(finish_reason) if finish_reason is not None else None,
        usage=_usage_from_response(response),
    )


def _extract_text(payload: Any) -> str:
    text = getattr(payload, "text", None)
    if isinstance(text, str) and text:
        return text

    candidates = getattr(payload, "candidates", None)
    if not candidates:
        return ""

    parts = getattr(candidates[0].content, "parts", [])
    collected: list[str] = []
    for part in parts:
        part_text = getattr(part, "text", None)
        if part_text:
            collected.append(part_text)

    return "".join(collected)


def _extract_stream_chunk(payload: Any) -> tuple[str, str | None]:
    content = _extract_text(payload)
    finish_reason: str | None = None
    candidates = getattr(payload, "candidates", None)
    if candidates:
        raw_finish_reason = getattr(candidates[0], "finish_reason", None)
        if raw_finish_reason is not None:
            finish_reason = str(raw_finish_reason)
    return content, finish_reason


@dataclass
class _NextChunkResult:
    done: bool
    payload: Any | None


def _next_chunk(iterator: Iterator[Any]) -> _NextChunkResult:
    try:
        return _NextChunkResult(done=False, payload=next(iterator))
    except StopIteration:
        return _NextChunkResult(done=True, payload=None)


class GeminiProvider:
    """LLMProvider adapter backed by Gemini via the google-genai SDK."""

    def __init__(self, api_key: str | None) -> None:
        self._client = genai.Client(api_key=api_key)

    def _generate_content_stream(
        self,
        *,
        model: str,
        contents: str | list[Any],
        system_instruction: str | None = None,
        temperature: float,
        max_tokens: int | None = None,
    ) -> Iterator[Any]:
        # google-genai's type stubs are broad; this wrapper keeps strict
        # type-checkers happy while preserving the SDK call shape.
        models_api = cast(Any, self._client.models)
        generate_content_stream = cast(
            Callable[..., Iterator[Any]],
            models_api.generate_content_stream,
        )
        config: dict[str, Any] = {"temperature": temperature}
        if system_instruction is not None:
            config["system_instruction"] = system_instruction
        if max_tokens is not None:
            config["max_output_tokens"] = max_tokens
        return generate_content_stream(
            model=model,
            contents=contents,
            config=config,
        )

    def _generate_content(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: int | None = None,
    ) -> Any:
        models_api = cast(Any, self._client.models)
        generate_content = cast(
            Callable[..., Any],
            models_api.generate_content,
        )
        config: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            config["max_output_tokens"] = max_tokens
        return generate_content(
            model=model,
            contents=prompt,
            config=config,
        )

    def _generate_content_with_tools(
        self,
        *,
        model: str,
        contents: list[Any],
        tools: list[Any],
        system_instruction: str | None,
        temperature: float,
        max_tokens: int | None = None,
    ) -> Any:
        models_api = cast(Any, self._client.models)
        generate_content = cast(
            Callable[..., Any],
            models_api.generate_content,
        )
        config: dict[str, Any] = {"temperature": temperature}
        if tools:
            config["tools"] = tools
        if system_instruction is not None:
            config["system_instruction"] = system_instruction
        if max_tokens is not None:
            config["max_output_tokens"] = max_tokens
        return generate_content(
            model=model,
            contents=contents,
            config=config,
        )

    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        # Unified chat may pass tool-loop history as dict messages after web
        # search; use the native contents API so those turns round-trip correctly.
        system_instruction, contents = _to_gemini_contents(
            cast(list[ChatMessageInput], messages)
        )
        stream = self._generate_content_stream(
            model=model,
            contents=contents,
            system_instruction=system_instruction,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        iterator = iter(stream)
        while True:
            result = await asyncio.to_thread(_next_chunk, iterator)
            if result.done:
                break

            content, finish_reason = _extract_stream_chunk(result.payload)
            if not content and finish_reason is None:
                continue

            yield ProviderChunk(content=content, finish_reason=finish_reason)

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> ProviderCompletion:
        prompt = _messages_to_prompt(messages)
        response = await asyncio.to_thread(
            self._generate_content,
            model=model,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return ProviderCompletion(
            content=_extract_text(response),
            usage=_usage_from_response(response),
        )

    async def complete_chat_with_tools(
        self,
        messages: list[ChatMessageInput],
        model: str,
        tools: list[dict[str, object]],
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> ProviderToolCompletion:
        system_instruction, contents = _to_gemini_contents(messages)
        gemini_tools = _to_gemini_tools(tools)
        response = await asyncio.to_thread(
            self._generate_content_with_tools,
            model=model,
            contents=contents,
            tools=gemini_tools,
            system_instruction=system_instruction,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return _extract_tool_completion(response)
