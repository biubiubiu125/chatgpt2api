from __future__ import annotations

import time
import uuid
from typing import Any, Iterable, Iterator

from fastapi import HTTPException

from services.protocol.chat_completion_cache import cache_key, chat_completion_cache, normalize_text_messages
from services.protocol.conversation import (
    ConversationRequest,
    ImageOutput,
    collect_text,
    count_message_image_tokens,
    count_message_text_tokens,
    count_text_tokens,
    encode_images,
    normalize_messages,
    stream_text_deltas,
    text_backend,
)
from services.protocol.reasoning import thinking_effort_from_body
from services.protocol import durable_image
from services.protocol.image_source_fingerprint import (
    image_source_markers_from_content,
    source_request_hash,
)
from services.protocol.web_search_tool import (
    WEB_SEARCH_TOOL_TYPES,
    has_unsupported_tools,
    is_web_search_chat_request,
    run_web_search,
    search_query_from_messages,
    text_with_url_citations,
)
from utils.helper import build_chat_image_markdown_content, extract_chat_image, extract_chat_prompt, is_image_chat_request, parse_image_count
from utils.image_tokens import (
    chat_usage_from_image_usage,
    count_image_inputs_tokens,
    count_image_output_items_tokens,
    image_usage,
)

TOOL_UNAVAILABLE_SYSTEM_MESSAGE = (
    "This compatibility backend cannot execute local tools, shell commands, non-search tools, "
    "or file operations. Do not claim to have run tools or inspected external resources. "
    "If a user asks you to use a tool, say that tool execution is unavailable through this backend."
)
_CHAT_IMAGE_ARGS_KEY = "_chat_image_args"
_DURABLE_IMAGE_REQUEST_KEY = "_durable_image_request"


def completion_chunk(model: str, delta: dict[str, Any], finish_reason: str | None = None, completion_id: str = "", created: int | None = None) -> dict[str, Any]:
    return {
        "id": completion_id or f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion.chunk",
        "created": created or int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def completion_response(
    model: str,
    content: str,
    created: int | None = None,
    messages: list[dict[str, Any]] | None = None,
    annotations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    prompt_text_tokens = count_message_text_tokens(messages, model) if messages else 0
    prompt_image_tokens = count_message_image_tokens(messages, model) if messages else 0
    prompt_tokens = prompt_text_tokens + prompt_image_tokens
    completion_tokens = count_text_tokens(content, model) if messages else 0
    message = {"role": "assistant", "content": content}
    if annotations:
        message["annotations"] = annotations
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": created or int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {
                "text_tokens": prompt_text_tokens,
                "image_tokens": prompt_image_tokens,
                "cached_tokens": 0,
            },
            "completion_tokens_details": {
                "text_tokens": completion_tokens,
                "image_tokens": 0,
                "reasoning_tokens": 0,
            },
        },
    }


def _with_log_metadata(
    payload: dict[str, Any],
    account_email: str = "",
    conversation_id: str = "",
    image_urls: Iterable[str] | None = None,
    image_attempts: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if account_email:
        payload["_account_email"] = account_email
    if conversation_id:
        payload["_conversation_id"] = conversation_id
    urls = [str(url).strip() for url in image_urls or [] if str(url).strip()]
    if urls:
        payload["_image_urls"] = list(dict.fromkeys(urls))
    attempts = [dict(item) for item in image_attempts or [] if isinstance(item, dict)]
    if attempts:
        payload["_image_attempts"] = attempts
    return payload


def _backend_account_email(backend: object) -> str:
    return str(getattr(backend, "account_email", "") or "").strip()


def stream_text_chat_completion(
    backend,
    messages: list[dict[str, Any]],
    model: str,
    thinking_effort: str = "",
) -> Iterator[dict[str, Any]]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    sent_role = False
    request = ConversationRequest(model=model, messages=messages, thinking_effort=thinking_effort)
    for delta_text in stream_text_deltas(backend, request):
        if not sent_role:
            sent_role = True
            yield _with_log_metadata(
                completion_chunk(model, {"role": "assistant", "content": delta_text}, None, completion_id, created),
                _backend_account_email(backend),
            )
        else:
            yield _with_log_metadata(
                completion_chunk(model, {"content": delta_text}, None, completion_id, created),
                _backend_account_email(backend),
            )
    if not sent_role:
        yield _with_log_metadata(
            completion_chunk(model, {"role": "assistant", "content": ""}, None, completion_id, created),
            _backend_account_email(backend),
        )
    yield _with_log_metadata(completion_chunk(model, {}, "stop", completion_id, created), _backend_account_email(backend))


def collect_chat_content(chunks: Iterable[dict[str, Any]]) -> str:
    parts: list[str] = []
    for chunk in chunks:
        choices = chunk.get("choices")
        first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
        content = str(delta.get("content") or "")
        if content:
            parts.append(content)
    return "".join(parts)


def chat_messages_from_body(body: dict[str, Any]) -> list[dict[str, Any]]:
    messages = body.get("messages")
    if isinstance(messages, list) and messages:
        valid: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                raise HTTPException(status_code=400, detail={"error": "messages must include valid role and content"})
            role = str(message.get("role") or "").strip().lower()
            content = message.get("content")
            has_content = (
                (isinstance(content, str) and bool(content.strip()))
                or (isinstance(content, list) and bool(content))
                or bool(message.get("tool_calls"))
            )
            if role not in {"system", "user", "assistant", "tool", "developer"} or not has_content:
                raise HTTPException(status_code=400, detail={"error": "messages must include valid role and content"})
            valid.append(message)
        return valid
    prompt = str(body.get("prompt") or "").strip()
    if prompt:
        return [{"role": "user", "content": prompt}]
    raise HTTPException(status_code=400, detail={"error": "messages or prompt is required"})


def chat_image_args(body: dict[str, Any]) -> tuple[str, str, int, list[tuple[bytes, str, str]], str | None]:
    cached = body.get(_CHAT_IMAGE_ARGS_KEY)
    if isinstance(cached, dict):
        images = cached.get("images")
        if isinstance(images, list):
            return (
                str(cached.get("model") or "gpt-image-2"),
                str(cached.get("prompt") or ""),
                int(cached.get("n") or 1),
                list(images),
                str(cached.get("base_url") or "").strip() or None,
            )
    model = str(body.get("model") or "gpt-image-2").strip() or "gpt-image-2"
    prompt = extract_chat_prompt(body)
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": "prompt is required"})
    images = [
        (data, f"image_{idx}.png", mime)
        for idx, (data, mime) in enumerate(extract_chat_image(body), start=1)
    ]
    base_url = str(body.get("base_url") or "").strip() or None
    body[_CHAT_IMAGE_ARGS_KEY] = {
        "model": model,
        "prompt": prompt,
        "n": parse_image_count(body.get("n")),
        "images": images,
        "base_url": base_url or "",
    }
    return model, prompt, int(body[_CHAT_IMAGE_ARGS_KEY]["n"]), images, base_url


def chat_image_source_markers(body: dict[str, Any]) -> list[dict[str, object]]:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return []
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        markers = image_source_markers_from_content(message.get("content"))
        if markers:
            return markers
    return []


def chat_image_source_request_hash(body: dict[str, Any]) -> str:
    prompt = extract_chat_prompt(body)
    if not prompt:
        return ""
    fingerprint = {
        "protocol": "openai.chat.completions",
        "prompt": prompt,
        "model": str(body.get("model") or "gpt-image-2").strip() or "gpt-image-2",
        "n": parse_image_count(body.get("n")),
        "size": body.get("size"),
        "quality": body.get("quality") or "auto",
        "response_format": "url",
        "image_sources": chat_image_source_markers(body),
    }
    return source_request_hash(fingerprint)


def durable_image_replay_fingerprint(body: dict[str, Any]) -> tuple[str, str]:
    markers = chat_image_source_markers(body)
    return ("edit" if markers else "generation", chat_image_source_request_hash(body))


def _durable_image_request_from_submission(body: dict[str, Any]) -> tuple[dict[str, Any], str, str] | None:
    submission = durable_image.attached_submission(body)
    if submission is None:
        return None
    payload = dict(submission.get("request_payload") or {})
    task_type = str(submission.get("task_type") or "").strip()
    mode = "edit" if task_type == "edit" or payload.get("images") or payload.get("input_artifacts") else "generation"
    response_format = str(submission.get("response_format") or payload.get("response_format") or "url")
    body[_DURABLE_IMAGE_REQUEST_KEY] = {
        "payload": payload,
        "mode": mode,
        "response_format": response_format,
    }
    return payload, mode, response_format


def _chat_image_args_from_payload(
    body: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, str, int, list[tuple[bytes, str, str]], str | None]:
    cached = body.get(_CHAT_IMAGE_ARGS_KEY)
    if isinstance(cached, dict) and isinstance(cached.get("images"), list):
        return (
            str(cached.get("model") or "gpt-image-2"),
            str(cached.get("prompt") or ""),
            int(cached.get("n") or 1),
            list(cached.get("images") or []),
            str(cached.get("base_url") or "").strip() or None,
        )
    images = payload.get("images")
    return (
        str(payload.get("model") or body.get("model") or "gpt-image-2").strip() or "gpt-image-2",
        str(payload.get("prompt") or extract_chat_prompt(body) or "").strip(),
        parse_image_count(payload.get("n") or body.get("n")),
        list(images) if isinstance(images, list) else [],
        str(payload.get("base_url") or body.get("base_url") or "").strip() or None,
    )


def text_chat_parts(body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    model = str(body.get("model") or "auto").strip() or "auto"
    messages = normalize_text_messages(normalize_messages(chat_messages_from_body(body)))
    if has_unsupported_tools(body, WEB_SEARCH_TOOL_TYPES):
        messages.insert(0, {"role": "system", "content": TOOL_UNAVAILABLE_SYSTEM_MESSAGE})
    return model, messages


def chat_completion_annotations(annotations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for item in annotations:
        if item.get("type") != "url_citation":
            continue
        output.append({
            "type": "url_citation",
            "url_citation": {
                "start_index": item.get("start_index", 0),
                "end_index": item.get("end_index", 0),
                "url": item.get("url", ""),
                "title": item.get("title", ""),
            },
        })
    return output


def web_search_chat_response(messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
    query = search_query_from_messages(messages)
    if not query:
        raise HTTPException(status_code=400, detail={"error": "messages or prompt is required for web search"})
    text, annotations = text_with_url_citations(run_web_search(query))
    return completion_response(
        model,
        text,
        messages=messages,
        annotations=chat_completion_annotations(annotations),
    )


def stream_web_search_chat_completion(messages: list[dict[str, Any]], model: str) -> Iterator[dict[str, Any]]:
    query = search_query_from_messages(messages)
    if not query:
        raise HTTPException(status_code=400, detail={"error": "messages or prompt is required for web search"})
    text, _annotations = text_with_url_citations(run_web_search(query))
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    yield completion_chunk(model, {"role": "assistant", "content": text}, None, completion_id, created)
    yield completion_chunk(model, {}, "stop", completion_id, created)


def image_result_content(result: dict[str, Any]) -> str:
    data = result.get("data")
    if isinstance(data, list) and data:
        return build_chat_image_markdown_content(result)
    return str(result.get("message") or "Image generation completed.")


def durable_image_request(body: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    cached = body.get(_DURABLE_IMAGE_REQUEST_KEY)
    if isinstance(cached, dict) and isinstance(cached.get("payload"), dict):
        return (
            dict(cached["payload"]),
            str(cached.get("mode") or "generation"),
            str(cached.get("response_format") or "url"),
        )
    attached = _durable_image_request_from_submission(body)
    if attached is not None:
        return attached
    model, prompt, n, images, base_url = chat_image_args(body)
    payload = {
        "prompt": prompt,
        "model": model,
        "n": n,
        "images": images,
        "size": body.get("size"),
        "quality": body.get("quality") or "auto",
        "base_url": base_url,
    }
    replay_hash = chat_image_source_request_hash(body)
    if replay_hash:
        payload["source_request_hash"] = replay_hash
    mode = "edit" if images else "generation"
    body[_DURABLE_IMAGE_REQUEST_KEY] = {
        "payload": payload,
        "mode": mode,
        "response_format": "url",
    }
    return payload, mode, "url"


def image_chat_response(body: dict[str, Any]) -> dict[str, Any]:
    if not durable_image.has_durable_context(body):
        from services.image_failure import ImageGenerationError, image_failure

        raise ImageGenerationError(
            "durable image task context is required",
            failure=image_failure(
                "durable_context_required",
                raw_detail="chat image requests must enter the PostgreSQL durable queue",
            ),
        )
    payload, mode, response_format = durable_image_request(body)
    model, prompt, n, images, base_url = _chat_image_args_from_payload(body, payload)
    result = durable_image.execute(
        body,
        payload,
        mode=mode,
        response_format=response_format,
    )
    response = completion_response(model, image_result_content(result), int(result.get("created") or 0) or None)
    task_id = str(result.get("task_id") or "")
    if task_id:
        response["task_id"] = task_id
    response["choices"][0]["message"]["image_results"] = list(result.get("data") or [])
    usage = image_usage(
        input_text_tokens=count_text_tokens(prompt, model),
        input_image_tokens=count_image_inputs_tokens(images, model),
        output_tokens=count_image_output_items_tokens(result.get("data")),
    )
    response["usage"] = chat_usage_from_image_usage(usage)
    _with_log_metadata(
        response,
        str(result.get("_account_email") or ""),
        str(result.get("_conversation_id") or ""),
        result.get("_image_urls") if isinstance(result.get("_image_urls"), list) else None,
        result.get("_image_attempts") if isinstance(result.get("_image_attempts"), list) else None,
    )
    return response


def image_chat_events(body: dict[str, Any]) -> Iterator[dict[str, Any]]:
    if not durable_image.has_durable_context(body):
        from services.image_failure import ImageGenerationError, image_failure

        raise ImageGenerationError(
            "durable image task context is required",
            failure=image_failure(
                "durable_context_required",
                raw_detail="chat image requests must enter the PostgreSQL durable queue",
            ),
        )
    payload, mode, response_format = durable_image_request(body)
    model, prompt, n, images, base_url = _chat_image_args_from_payload(body, payload)
    yield from stream_image_chat_completion(
        durable_image.stream_outputs(
            body,
            payload,
            mode=mode,
            response_format=response_format,
            model=model,
        ),
        model,
    )


def stream_image_chat_completion(image_outputs: Iterable[ImageOutput], model: str) -> Iterator[dict[str, Any]]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    sent_role = False
    sent_text = ""
    task_id = ""
    for output in image_outputs:
        task_id = output.task_id or task_id
        content = ""
        emit_empty_progress = False
        if output.kind == "progress":
            content = output.text
            emit_empty_progress = bool(output.task_id and not content)
            sent_text += content
        elif output.kind == "result":
            content = build_chat_image_markdown_content({"data": output.data})
        elif output.kind == "message":
            content = output.text[len(sent_text):] if output.text.startswith(sent_text) else output.text
        if not content and not emit_empty_progress:
            continue
        if not sent_role:
            sent_role = True
            delta = {"role": "assistant", "content": content}
            if output.kind == "result":
                delta["image_results"] = [dict(item) for item in output.data]
            chunk = completion_chunk(model, delta, None, completion_id, created)
            if output.task_id:
                chunk["task_id"] = output.task_id
            yield _with_log_metadata(
                chunk,
                output.account_email,
                output.conversation_id,
                output.image_urls,
                output.image_attempts,
            )
        else:
            delta = {"content": content}
            if output.kind == "result":
                delta["image_results"] = [dict(item) for item in output.data]
            chunk = completion_chunk(model, delta, None, completion_id, created)
            if output.task_id:
                chunk["task_id"] = output.task_id
            yield _with_log_metadata(
                chunk,
                output.account_email,
                output.conversation_id,
                output.image_urls,
                output.image_attempts,
            )
    if not sent_role:
        yield completion_chunk(model, {"role": "assistant", "content": ""}, None, completion_id, created)
    completed = completion_chunk(model, {}, "stop", completion_id, created)
    if task_id:
        completed["task_id"] = task_id
    yield completed


def text_completion_response(model: str, messages: list[dict[str, Any]], thinking_effort: str) -> dict[str, Any]:
    backend = text_backend()
    response = completion_response(
        model,
        collect_text(backend, ConversationRequest(model=model, messages=messages, thinking_effort=thinking_effort)),
        messages=messages,
    )
    return _with_log_metadata(response, _backend_account_email(backend))


def _validate_text_choice_count(body: dict[str, Any]) -> None:
    value = body.get("n")
    if value is None:
        return
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"error": "n must be an integer"}) from exc
    if count != 1:
        raise HTTPException(
            status_code=400,
            detail={"error": "n must be 1 for text chat completions"},
        )


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    if not is_image_chat_request(body):
        _validate_text_choice_count(body)
    if body.get("stream"):
        if is_image_chat_request(body):
            return image_chat_events(body)
        model, messages = text_chat_parts(body)
        if is_web_search_chat_request(body) and not has_unsupported_tools(body, WEB_SEARCH_TOOL_TYPES):
            return stream_web_search_chat_completion(messages, model)
        thinking_effort = thinking_effort_from_body(body)
        key = cache_key(body, messages, stream=True, protocol="chat")
        return chat_completion_cache.get_or_compute_stream(
            key,
            lambda: stream_text_chat_completion(text_backend(), messages, model, thinking_effort),
        )
    if is_image_chat_request(body):
        return image_chat_response(body)
    model, messages = text_chat_parts(body)
    if is_web_search_chat_request(body) and not has_unsupported_tools(body, WEB_SEARCH_TOOL_TYPES):
        return web_search_chat_response(messages, model)
    thinking_effort = thinking_effort_from_body(body)
    key = cache_key(body, messages, stream=False, protocol="chat")
    return chat_completion_cache.get_or_compute_response(
        key,
        lambda: text_completion_response(model, messages, thinking_effort),
    )
