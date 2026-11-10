import asyncio
import json

import httpx
import pytest
from agents import Agent, RunConfig, Runner, function_tool
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from app.worker.agent import XAI_BASE_URL
from app.worker.context import ConversationContext
from app.worker.execution import WorkerExecutionError, execute_request
from app.tools.task_tools import TASK_TOOLS


@function_tool
def echo_for_adapter_test(value: str) -> str:
    return f"echo: {value}"


def test_chat_completions_auth_and_tool_calls_use_grok_format() -> None:
    requests: list[httpx2.Request] = []
    request_bodies: list[dict[str, object]] = []

    async def grok_mock(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        request_bodies.append(json.loads(request.content))
        if len(requests) == 1:
            response_body = {
                "id": "chatcmpl-tool-call",
                "object": "chat.completion",
                "created": 1,
                "model": "grok-4.7",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-echo",
                                    "type": "function",
                                    "function": {
                                        "name": "echo_for_adapter_test",
                                        "arguments": '{"value":"hello"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }
        else:
            response_body = {
                "id": "chatcmpl-final",
                "object": "chat.completion",
                "created": 2,
                "model": "grok-4.7",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "The test tool returned echo: hello.",
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 15,
                    "completion_tokens": 7,
                    "total_tokens": 22,
                },
            }
        return httpx2.Response(200, json=response_body)

    async def run_mocked_request() -> str:
        transport = httpx2.MockTransport(grok_mock)
        http_client = httpx2.AsyncClient(transport=transport)
        client = AsyncOpenAI(
            api_key="test-grok-key",
            base_url=XAI_BASE_URL,
            http_client=http_client,
        )
        try:
            model = OpenAIChatCompletionsModel(
                model="grok-4.7",
                openai_client=client,
            )
            agent = Agent(
                name="Adapter test",
                instructions="Use the provided test tool and report its result.",
                model=model,
                tools=[echo_for_adapter_test],
                handoffs=[],
                mcp_servers=[],
            )
            result = await Runner.run(
                agent,
                "Echo hello.",
                max_turns=3,
                run_config=RunConfig(tracing_disabled=True),
            )
            return result.final_output
        finally:
            await client.close()

    response = asyncio.run(run_mocked_request())

    assert response == "The test tool returned echo: hello."
    assert len(requests) == 2
    assert all(
        str(request.url) == f"{XAI_BASE_URL}/chat/completions"
        for request in requests
    )
    assert all(
        request.headers["authorization"] == "Bearer test-grok-key"
        for request in requests
    )
    serialized_tools = request_bodies[0]["tools"]
    assert isinstance(serialized_tools, list)
    assert serialized_tools[0]["function"]["name"] == "echo_for_adapter_test"
    assert request_bodies[0]["model"] == "grok-4.7"
    assert request_bodies[1]["messages"][-1]["content"] == "echo: hello"


def test_grok_authentication_failure_is_sanitized(caplog) -> None:
    async def unauthorized_response(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            401,
            json={
                "error": {
                    "message": "provider returned private authentication details",
                    "type": "authentication_error",
                    "code": "invalid_api_key",
                }
            },
        )

    async def run_mocked_failure() -> None:
        http_client = httpx2.AsyncClient(
            transport=httpx2.MockTransport(unauthorized_response)
        )
        client = AsyncOpenAI(
            api_key="test-grok-key",
            base_url=XAI_BASE_URL,
            max_retries=0,
            http_client=http_client,
        )
        try:
            model = OpenAIChatCompletionsModel(
                model="grok-4.7",
                openai_client=client,
            )
            agent = Agent(name="Adapter test", instructions="Reply.", model=model)
            with pytest.raises(WorkerExecutionError, match="could not complete") as error:
                await execute_request(
                    agent=agent,
                    user_message="Hello.",
                    conversation=ConversationContext(),
                    max_turns=2,
                )
            assert "private authentication details" not in str(error.value)
            assert "provider_status_code=401" in caplog.text
            assert "private authentication details" not in caplog.text
        finally:
            await client.close()

    asyncio.run(run_mocked_failure())


def test_registered_task_tool_schemas_serialize_for_grok() -> None:
    request_bodies: list[dict[str, object]] = []

    async def grok_mock(request: httpx2.Request) -> httpx2.Response:
        request_bodies.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-task-tools",
                "object": "chat.completion",
                "created": 3,
                "model": "grok-4.7",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "Ready to help manage your tasks.",
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 15,
                    "completion_tokens": 6,
                    "total_tokens": 21,
                },
            },
        )

    async def run_mocked_request() -> None:
        http_client = httpx2.AsyncClient(
            transport=httpx2.MockTransport(grok_mock)
        )
        client = AsyncOpenAI(
            api_key="test-grok-key",
            base_url=XAI_BASE_URL,
            http_client=http_client,
        )
        try:
            agent = Agent(
                name="Task tool schema test",
                instructions="Reply briefly.",
                model=OpenAIChatCompletionsModel(
                    model="grok-4.7",
                    openai_client=client,
                ),
                tools=list(TASK_TOOLS),
                handoffs=[],
                mcp_servers=[],
            )
            result = await Runner.run(
                agent,
                "Hello.",
                max_turns=2,
                run_config=RunConfig(tracing_disabled=True),
            )
            assert result.final_output == "Ready to help manage your tasks."
        finally:
            await client.close()

    asyncio.run(run_mocked_request())

    assert len(request_bodies) == 1
    tool_definitions = request_bodies[0]["tools"]
    assert isinstance(tool_definitions, list)
    assert {
        definition["function"]["name"] for definition in tool_definitions
    } == {
        "create_task",
        "list_tasks",
        "get_task",
        "update_task",
        "complete_task",
    }
