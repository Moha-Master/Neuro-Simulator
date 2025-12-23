"""API endpoints for the Neuro Sama module."""

import asyncio
import json
from typing import Dict, Any, List

import json
import os
from pathlib import Path
import yaml
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from openai import AsyncOpenAI

from .config import Config
from .context_builder import ContextBuilder
from .json_stream_parser import StreamingJSONParser


router = APIRouter()

# Global variable to track if the module is currently processing
is_processing = False


def parse_json_response(response_text: str) -> List[Dict[str, Any]]:
    """Parse the JSON response from the LLM."""
    try:
        # Try to find JSON array in the response
        start_idx = response_text.find('[')
        end_idx = response_text.rfind(']') + 1

        if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
            json_str = response_text[start_idx:end_idx]
            parsed = json.loads(json_str)
            return parsed if isinstance(parsed, list) else [parsed]
        else:
            # If no array found, try to parse the whole response as a single object
            parsed = json.loads(response_text.strip())
            return [parsed] if isinstance(parsed, dict) else parsed
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON response: {e}")
        print(f"Response text: {response_text}")
        return []


async def execute_tool_and_get_output_pack(context_builder, tool_call: Dict[str, Any], input_data: Dict[str, Any] = None):
    """Execute a single tool and return an output pack if applicable."""
    if not isinstance(tool_call, dict):
        return None

    tool_name = tool_call.get("name")
    tool_params = tool_call.get("params") or tool_call.get("parameters", {})

    if not tool_name:
        return None

    # Get the tool from the context builder's tool manager
    tool = context_builder.tool_manager.get_tool(tool_name)
    if not tool:
        print(f"Unknown tool: {tool_name}")
        return None

    try:
        result = await tool.execute(**tool_params)

        # If this is a speak tool, create an output pack with TTS
        if tool_name == "speak":
            spoken_text = result.get("spoken_text", "")
            if spoken_text:
                # Synthesize audio with TTS
                audio_base64 = ""
                duration = 0.0
                try:
                    from .tts import synthesize_audio_segment
                    audio_base64, duration = await synthesize_audio_segment(spoken_text, context_builder.config)
                except Exception as e:
                    print(f"TTS synthesis failed: {e}")
                    # Continue with empty audio as fallback

                # Create an output pack for speak
                from .output_manager import OutputManager
                output_pack = OutputManager.create_speak_output(
                    text=spoken_text,
                    audio_base64=audio_base64,
                    duration=duration,
                    input_data=input_data
                )
                return output_pack
    except Exception as e:
        print(f"Error executing tool {tool_name}: {e}")

    return None


async def handle_websocket_communication(websocket: WebSocket, client: AsyncOpenAI, config: Config):
    """Handle the input/output communication via WebSocket."""
    global is_processing

    # Initialize context builder
    context_builder = ContextBuilder(config)

    # Initialize streaming JSON parser
    json_parser = StreamingJSONParser()

    # In-memory storage for recent messages (for this session)
    recent_messages: List[Dict[str, str]] = []

    await websocket.accept()

    try:
        while True:
            # Receive a message from the client
            data = await websocket.receive_json()

            # If currently processing, ignore the new input
            if is_processing:
                await websocket.send_json({
                    "type": "error",
                    "message": "Module is currently processing, please wait"
                })
                continue

            # Set processing flag
            is_processing = True

            try:
                user_message = data.get("content", "")
                user_name = data.get("user", "user")

                # Add the user message to recent messages
                recent_messages.append({
                    "user": user_name,
                    "content": user_message
                })

                # Limit recent messages to prevent it from growing indefinitely
                if len(recent_messages) > 20:  # Keep last 20 messages
                    recent_messages.pop(0)

                # Build the context using the context builder
                context = context_builder.build_context(recent_messages)

                # Prepare messages for the API call
                messages = [
                    {"role": "system", "content": context},
                ]

                # Call the OpenAI API to get a response
                response = await client.chat.completions.create(
                    model=config.OPENAI_MODEL,
                    messages=messages,
                    stream=True
                )

                # Stream the response and process JSON objects as they are completed
                print("DEBUG: Starting to stream response...")
                full_content = ""
                async for chunk in response:
                    if chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        full_content += content
                        print(f"DEBUG: Received content chunk: {repr(content)}")

                        # Feed the content to the streaming JSON parser
                        complete_objects = json_parser.feed(content)
                        print(f"DEBUG: Found {len(complete_objects)} complete objects in chunk")

                        # Process any complete JSON objects
                        for i, obj in enumerate(complete_objects):
                            print(f"DEBUG: Processing complete object {i+1}: {obj}")
                            output_pack = await execute_tool_and_get_output_pack(context_builder, obj, data)
                            if output_pack:
                                print(f"DEBUG: Sending output pack: {output_pack}")
                                await websocket.send_json(output_pack)
                            else:
                                print(f"DEBUG: No output pack from object: {obj}")

                print(f"DEBUG: Full content received: {full_content}")
                print(f"DEBUG: Remaining buffer in parser: {json_parser.get_remaining_buffer()}")

                # Send a completion marker to indicate the end of this response
                from .output_manager import OutputManager
                completion_pack = OutputManager.create_completion_output(data)
                await websocket.send_json(completion_pack)

            finally:
                # Reset processing flag
                is_processing = False

    except WebSocketDisconnect:
        print("WebSocket disconnected")
    except Exception as e:
        print(f"Error in WebSocket communication: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Error processing message: {str(e)}"
            })
        except:
            pass  # If we can't send the error, just continue


@router.websocket("/ws/chat")
async def websocket_chat_endpoint(websocket: WebSocket):
    """WebSocket endpoint for chat functionality."""
    # Get the OpenAI client and config from app state
    client = websocket.app.state.openai_client
    config = websocket.app.state.config

    await handle_websocket_communication(websocket, client, config)


@router.websocket("/ws/admin")
async def websocket_admin_endpoint(websocket: WebSocket):
    """Management WebSocket endpoint for configuration updates and module control."""
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            # 处理管理消息
            action = message.get("action")
            payload = message.get("payload", {})

            if action == "reload_config":
                # 重新加载配置
                working_dir = os.getenv("NEURO_WORKING_DIR")
                if not working_dir:
                    # 如果环境变量没有设置，尝试从应用状态获取
                    if hasattr(websocket.app.state, 'config') and hasattr(websocket.app.state.config, 'PROMPT_PATH'):
                        # 从当前配置中推断工作目录
                        current_prompt_path = websocket.app.state.config.PROMPT_PATH
                        working_path = Path(current_prompt_path).parent.parent  # Go up from prompts/neuro_prompt.txt to neuro_sama/
                        working_dir = str(working_path)
                    else:
                        await websocket.send_text(json.dumps({
                            "type": "response",
                            "request_id": message.get("request_id"),
                            "payload": {"status": "error", "message": "Working directory not set and cannot be inferred"}
                        }))
                        return

                working_path = Path(working_dir)
                config_file = working_path.parent / "config.yaml"
                if config_file.exists():
                    with open(config_file, 'r', encoding='utf-8') as f:
                        global_config = yaml.safe_load(f)

                    # 更新配置
                    # 通过 websocket 对象访问应用状态
                    websocket.app.state.config = Config(global_config, working_dir)
                    websocket.app.state.openai_client = AsyncOpenAI(
                        api_key=websocket.app.state.config.OPENAI_API_KEY,
                        base_url=websocket.app.state.config.OPENAI_BASE_URL
                    )

                    await websocket.send_text(json.dumps({
                        "type": "response",
                        "request_id": message.get("request_id"),
                        "payload": {"status": "success", "message": "Configuration reloaded"}
                    }))
                else:
                    await websocket.send_text(json.dumps({
                        "type": "response",
                        "request_id": message.get("request_id"),
                        "payload": {"status": "error", "message": "Config file not found"}
                    }))
            else:
                await websocket.send_text(json.dumps({
                    "type": "response",
                    "request_id": message.get("request_id"),
                    "payload": {"status": "error", "message": f"Unknown action: {action}"}
                }))
    except WebSocketDisconnect:
        print("Admin WebSocket disconnected")
    except Exception as e:
        print(f"Admin WebSocket error: {e}")