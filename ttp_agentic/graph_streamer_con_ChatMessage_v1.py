# %%
import os
import sys
from pathlib import Path

# Set working directory to file location
file_path = Path(__file__).resolve()
os.chdir(file_path.parent)
sys.path.append(str(file_path.parent.parent))
import asyncio
import json
import typing
from collections.abc import AsyncGenerator
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from IPython.display import Image, display
from langchain_core.messages import (AIMessage, BaseMessage, HumanMessage,
                                     ToolCall, ToolMessage, message_to_dict,
                                     messages_from_dict)
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from multiagent_graph_nuevo_context_manager_v2 import graph
from pydantic import BaseModel, Field

# display(Image(graph.get_graph().draw_mermaid_png()))
app = FastAPI()

def convert_message_content_to_string(content: str | list[str | dict]) -> str:
    if isinstance(content, str):
        return content
    text: list[str] = []
    for content_item in content:
        if isinstance(content_item, str):
            text.append(content_item)
            continue
        if content_item["type"] == "text":
            text.append(content_item["text"])
    return "".join(text)

class UserInput(BaseModel):
    """Basic user input for the agent."""

    message: str = Field(
        description="User input to the agent.",
        examples=["hola, que puedes hacer?"],
    )
    model: str = Field(
        description="LLM Model to use for the agent.",
        default="gpt-4o",
        examples=["gpt-4o", "gpt-4o-mini", "llama-3.1-70b"],
    )
    thread_id: str | None = Field(
        description="Thread ID to persist and continue a multi-turn conversation.",
        default=None,
        examples=["847c6285-8fc9-4560-a83f-4e6285809254"],
    )


class StreamInput(UserInput):
    """User input for streaming the agent's response."""

    stream_tokens: bool = Field(
        description="Whether to stream LLM tokens to the client.",
        default=True,
    )


def _parse_input(user_input: UserInput) -> tuple[dict[str, Any], str]:
    run_id = uuid4()
    thread_id = user_input.thread_id or str(uuid4())
    kwargs = {
        "input": {"messages": [HumanMessage(content=user_input.message)]},
        "config": RunnableConfig(
            configurable={"thread_id": thread_id, "model": user_input.model},
            run_id=run_id,
        ),
    }
    return kwargs, run_id
class ChatMessage(BaseModel):
    """Message in a chat."""

    type: Literal["human", "ai", "tool"] = Field(
        description="Role of the message.",
        examples=["human", "ai", "tool"],
    )
    content: str = Field(
        description="Content of the message.",
        examples=["Hello, world!"],
    )
    tool_calls: list[ToolCall] = Field(
        description="Tool calls in the message.",
        default=[],
    )
    tool_call_id: str | None = Field(
        description="Tool call that this message is responding to.",
        default=None,
        examples=["call_Jja7J89XsjrOLA5r!MEOW!SL"],
    )
    run_id: str | None = Field(
        description="Run ID of the message.",
        default=None,
        examples=["847c6285-8fc9-4560-a83f-4e6285809254"],
    )
    original: dict[str, Any] = Field(
        description="Original LangChain message in serialized form.",
        default={},
    )

    @classmethod
    def from_langchain(cls, message: BaseMessage) -> "ChatMessage":
        """Create a ChatMessage from a LangChain message."""
        original = message_to_dict(message)
        match message:
            case HumanMessage():
                human_message = cls(
                    type="human",
                    content=convert_message_content_to_string(message.content),
                    original=original,
                )
                return human_message
            case AIMessage():
                ai_message = cls(
                    type="ai",
                    content=convert_message_content_to_string(message.content),
                    original=original,
                )
                if message.tool_calls:
                    ai_message.tool_calls = message.tool_calls
                return ai_message
            case ToolMessage():
                tool_message = cls(
                    type="tool",
                    content=convert_message_content_to_string(message.content),
                    tool_call_id=message.tool_call_id,
                    original=original,
                )
                return tool_message
            case _:
                raise ValueError(f"Unsupported message type: {message.__class__.__name__}")

    def to_langchain(self) -> BaseMessage:
        """Convert the ChatMessage to a LangChain message."""
        if self.original:
            raw_original = messages_from_dict([self.original])[0]
            raw_original.content = self.content
            return raw_original
        match self.type:
            case "human":
                return HumanMessage(content=self.content)
            case _:
                raise NotImplementedError(f"Unsupported message type: {self.type}")

    def pretty_print(self) -> None:
        """Pretty print the ChatMessage."""
        lc_msg = self.to_langchain()
        lc_msg.pretty_print()

async def call_graph(graph: CompiledStateGraph, user_input: StreamInput) -> None:
    kwargs, run_id = _parse_input(user_input)
    config = {
        "configurable": {"thread_id": kwargs["config"]["configurable"]["thread_id"]}
    }
    print(graph.get_state(config).next)
    mensaje_del_usuario = kwargs["input"]["messages"][-1]
    node_to_stream: str = "agent"
    if graph.get_state(config).next == () or not graph.get_state(config).next:
        # Process streamed events from the graph and yield messages over the SSE stream.
        async for event in graph.astream_events(
            {"messages": [mensaje_del_usuario]}, config, version="v2"
        ):
            if not event:
                continue

            # Yield messages written to the graph state after node execution finishes.
            if (
                event["event"] == "on_chain_end"
                # on_chain_end gets called a bunch of times in a graph execution
                # This filters out everything except for "graph node finished"
                and any(t.startswith("graph:step:") for t in event.get("tags", []))
                and "messages" in event["data"]["output"]
                and event["metadata"].get("langgraph_node", "") == node_to_stream
            ):
                new_messages = event["data"]["output"]["messages"]


                print(f"#### 11111 {new_messages=}")
                for message in new_messages:

                    print(f"1111 {message=}")
                    message.pretty_print()
                    try:
                        chat_message = ChatMessage.from_langchain(message)
                        print(f"1111 {chat_message=}")
                        chat_message.run_id = str(run_id)
                        print(f"1111 {chat_message.run_id=}")
                    except Exception as e:
                        yield f"data: {json.dumps({'type': 'error', 'content': f'Error parsing message: {e}'})}\n\n"
                        continue
                if chat_message.model_dump()["tool_calls"]:
                    # print("-----------------------------------------------------------------")
                    # print(f"{chat_message.model_dump()['tool_calls']=}")
                    # print("-----------------------------------------------------------------")  
                    # print(f"{chat_message.model_dump()['tool_calls'][0]['args']['question']=}")
                    # print("-----------------------------------------------------------------")
                    # print(f"{chat_message.model_dump()['content']=}")
                    # print("-----------------------------------------------------------------")
                    # print(f"{chat_message.tool_calls[0]['args']['question']=}")
                    # print("-----------------------------------------------------------------")
                    if "question" in chat_message.tool_calls[0]["args"]:
                        chat_message.content = chat_message.tool_calls[0]["args"]["question"]
                    else:
                        chat_message.content = str(chat_message.tool_calls[0]["args"])
                # LangGraph re-sends the input message, which feels weird, so drop it
                if chat_message.type == "human":
                    continue

                # TODO: reconsider this
                # Currently drop the tool messages
                # if chat_message.type == "tool":
                #     continue
                # # if chat_message.type == "function":
                # #     continue
                # # if chat_message.type == "tool_call":
                # #     continue
                # # Drop the AI messages with empty content
                # if chat_message.type == "ai" and chat_message.content == "":
                #     continue

                # # Drop the AI, Tool, messages
                # if chat_message.type != "human":
                #     continue
                if chat_message.type:
                    print("##################################################################")
                    print(f"######################## {chat_message.type=} ###################")
                    print("##################################################################")
                    print("##################################################################")
                    print("##################################################################")
                    if chat_message.model_dump()["tool_calls"]:
                        if "input_string" in chat_message.tool_calls[0]["args"]:
                            print(f"{chat_message.tool_calls[0]['args']['input_string']=}")
                            continue
                    try:
                        yield f"data: {json.dumps({'type': 'message', 'content': chat_message.model_dump()})}\n\n"
                    except Exception as e:
                        print(f"Error: {e}")
                        continue
                else:
                    continue
                # if "tool_calls" in event["data"]["chunk"].additional_kwargs:
                #     tool_calls = event["data"]["chunk"].additional_kwargs["tool_calls"]
                #     yield ''#tool_calls[0]["function"]["arguments"]
                # else:
                #     yield ''#event["data"]["chunk"].content
            # print(graph.get_state(config).next)
        yield "data: [DONE]\n\n"

    else:
        async for event in graph.astream_events(
            Command(resume=mensaje_del_usuario), config, version="v2"
        ):
            if not event:
                continue
            if (
                event["event"] == "on_chain_end"
                # on_chain_end gets called a bunch of times in a graph execution
                # This filters out everything except for "graph node finished"
                and any(t.startswith("graph:step:") for t in event.get("tags", []))
                and "messages" in event["data"]["output"]
                and event["metadata"].get("langgraph_node", "") == node_to_stream

            ):
                new_messages = event["data"]["output"]["messages"]
                print(f"####  2222 {new_messages=}")
                for message in new_messages:
                    print(f"2222 {message=}")
                    message.pretty_print()
                    try:
                        chat_message = ChatMessage.from_langchain(message)
                        print(f"2222 {chat_message=}")
                        chat_message.run_id = str(run_id)
                        print(f"2222 {chat_message.run_id=}")
                    except Exception as e:
                        yield f"data: {json.dumps({'type': 'error', 'content': f'Error parsing message: {e}'})}\n\n"
                        continue
                # LangGraph re-sends the input message, which feels weird, so drop it
                # if chat_message.type == "human" and chat_message.content == user_input.message:
                #     continue

                # TODO: reconsider this
                # Currently drop the tool messages
                # if chat_message.type == "tool":
                #     continue

                # # Drop the AI messages with empty content
                # if chat_message.type == "ai" and chat_message.content == "":
                #     continue

                # # Drop the AI, Tool, messages
                # if chat_message.type != "human":
                #     continue
                try:
                    yield f"data: {json.dumps({'type': 'message', 'content': chat_message.model_dump()})}\n\n"
                except Exception as e:
                    print(f"Error: {e}")
                    continue

        yield "data: [DONE]\n\n"

preprompt = "Considera las oficinas ['001 - Huerfanos 740 EDW', '003 - Cauquenes', '004 - Apoquindo EDW', '009 - Vitacura EDW'] "
qs = [
    preprompt+"dame los detalles del mejor ejecutivo",
    preprompt+"septiembre",
    preprompt+"que datos hay disponibles?",
    preprompt+"dame el SLA de octubre",
    preprompt+"dame el ranking de ejecutivos de octubre",
    preprompt+"dame los detalles del peor ejecutivo de octubre",
    preprompt+"gracias",
    preprompt+"cual fue mi primera pregunta?",
    preprompt+"hola",
    preprompt+"dame los detalles del mejor ejecutivo",
    preprompt+"octubre",
    preprompt+"hola",
    preprompt+"dame el SLA de todo el mes de noviembre",
    preprompt+"ahora los ejecutivos",
    preprompt+"necesito el reporte",
    preprompt+"para el mes de septiembre",
    preprompt+"resuma nuestra conversación",
    preprompt+"que registros hay",
    preprompt+ "que herramientas tienes?",
    preprompt+ "dame el reporte general de oficinas",

]


#%%
user_input = UserInput(message=qs[1], model="gpt-4o", thread_id="0")

async for content in call_graph(graph, user_input):
    print(content, end="|")


# %%
# user_input = UserInput(message=qs[3], model="gpt-4o", thread_id="0")
# async for content in call_graph(graph, user_input):
#     print(content, end="|")


# # %%
# config = {"configurable": {"thread_id": "3"}}
# input_message = HumanMessage(content="hola")
# async for event in graph.astream_events(
#     {"messages": [input_message]}, config, version="v2"
# ):
#     print(
#         f"Node: {event['metadata'].get('langgraph_node','')}. Type: {event['event']}. Name: {event['name']}"
#     )

# %%
config = {"configurable": {"thread_id": "4"}}

print(graph.get_state(config).next)
node_to_stream = "agent"
input_message = HumanMessage(content="hola")
async for event in graph.astream_events(
    {"messages": [input_message]}, config, version="v2"
):
    # Get chat model tokens from a particular node
    if (
        event["event"] == "on_chat_model_stream"
        and event["metadata"].get("langgraph_node", "") == node_to_stream
    ):
        # print(event["data"])
        if "tool_calls" in event["data"]["chunk"].additional_kwargs:
            tool_calls = event["data"]["chunk"].additional_kwargs["tool_calls"]
            print(tool_calls[0]["function"]["arguments"])
        #     for tool_call in tool_calls:
        #         if "arguments" in tool_call.get("function", {}):
        #             content = tool_call["function"]["arguments"]
        #             if content and isinstance(content, str):
        #                 print(content)
print(graph.get_state(config).next)
# %%
input_message = HumanMessage(content="alejandro")
async for event in graph.astream_events(
    Command(resume=input_message), config, version="v2"
):
    # Get chat model tokens from a particular node
    if (
        event["event"] == "on_chat_model_stream"
        and event["metadata"].get("langgraph_node", "") == node_to_stream
    ):
        print(event["data"])

print(graph.get_state(config).next)

# %%

# %%


def _sse_response_example() -> dict[int, Any]:
    return {
        status.HTTP_200_OK: {
            "description": "Server Sent Event Response",
            "content": {
                "text/event-stream": {
                    "example": "data: {'type': 'token', 'content': 'Hello'}\n\ndata: {'type': 'token', 'content': ' World'}\n\ndata: [DONE]\n\n",
                    "schema": {"type": "string"},
                }
            },
        }
    }


@app.post(
    "/stream", response_class=StreamingResponse, responses=_sse_response_example()
)
async def stream_agent(user_input: StreamInput) -> StreamingResponse:

    async def generate() -> typing.AsyncIterator[str]:
        accumulated_content = []
        async for content in call_graph(graph, user_input):
            accumulated_content.append(content)
            yield f'data: {{"type": "token", "content": "{content}"}}\n\n'
        full_message = "".join(accumulated_content)
        yield f'data: {{"type": "message", "content": "{full_message}"}}\n\n'
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ADD CORS

cors_origins = [
    "http://localhost",
    "http://localhost:8080",
    "http://localhost:3000",
    "https://t-agentic-stage.ngrok.dev",
    "http://127.0.0.1:4040",
    "http://127.0.0.1:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=r"http://localhost(:\d+)?",  # Permite http://localhost con cualquier puerto
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

"""
curl -X 'POST' \
  'http://0.0.0.0:8080/stream' \
  -H 'accept: text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
  "message": "hola",
  "model": "gpt-4o",
  "thread_id": "847c6285-8fc9-4560-a83f-4e6285809254",
  "stream_tokens": true
}'


data: {"type": "token", "content": "\u00a1"}

data: {"type": "token", "content": "Hola"}

data: {"type": "token", "content": "!"}

data: {"type": "token", "content": " \u00bf"}

data: {"type": "token", "content": "En"}

data: {"type": "token", "content": " qu\u00e9"}

data: {"type": "token", "content": " puedo"}

data: {"type": "token", "content": " ayudarte"}

data: {"type": "token", "content": " hoy"}

data: {"type": "token", "content": "?"}

data: {"type": "token", "content": " Estoy"}

data: {"type": "token", "content": " aqu\u00ed"}

data: {"type": "token", "content": " para"}

data: {"type": "token", "content": " proporcion"}

data: {"type": "token", "content": "arte"}

data: {"type": "token", "content": " informaci\u00f3n"}

data: {"type": "token", "content": " y"}

data: {"type": "token", "content": " an\u00e1lisis"}

data: {"type": "token", "content": " sobre"}

data: {"type": "token", "content": " el"}

data: {"type": "token", "content": " desempe\u00f1o"}

data: {"type": "token", "content": " de"}

data: {"type": "token", "content": " suc"}

data: {"type": "token", "content": "urs"}

data: {"type": "token", "content": "ales"}

data: {"type": "token", "content": ","}

data: {"type": "token", "content": " tiempos"}

data: {"type": "token", "content": " de"}

data: {"type": "token", "content": " atenci\u00f3n"}

data: {"type": "token", "content": ","}

data: {"type": "token", "content": " niveles"}

data: {"type": "token", "content": " de"}

data: {"type": "token", "content": " servicio"}

data: {"type": "token", "content": ","}

data: {"type": "token", "content": " y"}

data: {"type": "token", "content": " m\u00e1s"}

data: {"type": "token", "content": "."}

data: {"type": "message", "content": {"type": "ai", "content": "\u00a1Hola! \u00bfEn qu\u00e9 puedo ayudarte hoy? Estoy aqu\u00ed para proporcionarte informaci\u00f3n y an\u00e1lisis sobre el desempe\u00f1o de sucursales, tiempos de atenci\u00f3n, niveles de servicio, y m\u00e1s.", "tool_calls": [], "tool_call_id": null, "run_id": "6a0170fb-0908-4a9e-941e-7cc9bbfdb97e", "original": {"type": "ai", "data": {"content": "\u00a1Hola! \u00bfEn qu\u00e9 puedo ayudarte hoy? Estoy aqu\u00ed para proporcionarte informaci\u00f3n y an\u00e1lisis sobre el desempe\u00f1o de sucursales, tiempos de atenci\u00f3n, niveles de servicio, y m\u00e1s.", "additional_kwargs": {}, "response_metadata": {"finish_reason": "stop", "model_name": "gpt-4o-2024-11-20", "system_fingerprint": "fp_82ce25c0d4"}, "type": "ai", "name": null, "id": "run-92880019-a2d0-4bc9-af2d-a29ec83c14bb", "example": false, "tool_calls": [], "invalid_tool_calls": [], "usage_metadata": null}}}}

data: [DONE]

"""

# config = {"configurable": {"thread_id": "0"}}
# user_input = HumanMessage(content="dame el reporte general de oficinas")
# async_generator = call_graph(graph, user_input, config)
# # %%
# async for content in async_generator:
#     print(content, end="|")

# # %%

# user_input = HumanMessage(content="para el ultimo mes")
# async_generator = call_graph(graph, user_input, config)

# async for content in async_generator:
#     print(content, end="|")
# # %%
# user_input = HumanMessage(content="dame el ranking de ejecutivos")
# async_generator = call_graph(graph, user_input, config)

# async for content in async_generator:
#     print(content, end="|")
# # %%
# user_input = HumanMessage(content="para el ultimo mes")
# async_generator = call_graph(graph, user_input, config)

# async for content in async_generator:
#     print(content, end="|")

# # %%
# config = {"configurable": {"thread_id": "3"}}
# for event in graph.stream(
#     {"messages": [HumanMessage(content="hola")]},
#     config,
#     stream_mode="updates",
# ):
#     print(event["agent"]["messages"][-1].pretty_print())
#     print(graph.get_state(config).next == ())
# # %%
# config = {"configurable": {"thread_id": "4"}}
# user_input = HumanMessage(content="hola")
# node_to_stream = "agent"
# async for event in graph.astream_events(
#     {"messages": [user_input]}, config, version="v2"
# ):
#     # Get chat model tokens from node_to_stream
#     if (
#         event["event"] == "on_chat_model_stream"
#         and event["metadata"].get("langgraph_node", "") == node_to_stream
#     ):
#         data = event["data"]
#         print(data["chunk"].content, end="1")
# # %%
