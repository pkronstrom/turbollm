import json

from turbollm.mlx_vlm_tool_proxy import (
    _stream_from_chat_response,
    parse_bare_gemma_tool_call,
    rewrite_chat_response,
)


def _chat_response(content="call:ls{path:.}", **overrides):
    response = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "diffusiongemma",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }
    response.update(overrides)
    return response


def test_parse_bare_gemma_tool_call_converts_unquoted_value_to_json_arguments():
    call = parse_bare_gemma_tool_call("call:ls{path:.}")

    assert call["type"] == "function"
    assert call["function"]["name"] == "ls"
    assert call["function"]["arguments"] == '{"path": "."}'


def test_parse_bare_gemma_tool_call_tolerates_missing_closing_brace():
    call = parse_bare_gemma_tool_call("call:ls{path:.")

    assert call["function"]["name"] == "ls"
    assert call["function"]["arguments"] == '{"path": "."}'


def test_rewrite_chat_response_moves_bare_call_text_to_tool_calls():
    rewritten = rewrite_chat_response(_chat_response())

    message = rewritten["choices"][0]["message"]
    assert rewritten["choices"][0]["finish_reason"] == "tool_calls"
    assert message["content"] is None
    assert message["tool_calls"][0]["function"]["name"] == "ls"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"path": "."}'


def test_stream_from_chat_response_uses_indexed_tool_call_delta():
    response = rewrite_chat_response(_chat_response())

    events = [
        line.removeprefix("data: ")
        for line in _stream_from_chat_response(response).decode().splitlines()
        if line.startswith("data: ")
    ]

    first = json.loads(events[0])
    tool_call = first["choices"][0]["delta"]["tool_calls"][0]
    assert first["choices"][0]["finish_reason"] is None
    assert tool_call["index"] == 0
    assert tool_call["function"]["name"] == "ls"

    second = json.loads(events[1])
    assert second["choices"][0]["delta"] == {}
    assert second["choices"][0]["finish_reason"] == "tool_calls"
    assert events[2] == "[DONE]"
