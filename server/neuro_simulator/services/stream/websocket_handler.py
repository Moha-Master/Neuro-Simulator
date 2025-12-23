"""
WebSocket handler for the stream service.
Handles client connections for the live stream.
"""
import asyncio
import json
import logging
from typing import Dict, Any
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from ...utils.state import app_state
from ...core.config import config_manager
from ...utils.websocket import connection_manager
from ...utils.queue import add_to_audience_buffer, add_to_neuro_input_queue, get_recent_audience_chats

logger = logging.getLogger(__name__)


class StreamWebSocketHandler:
    """Handles WebSocket connections for the live stream."""
    
    def __init__(self, stream_manager):
        self.stream_manager = stream_manager

    async def handle_websocket(self, websocket: WebSocket):
        """Handle a WebSocket connection for the stream."""
        assert config_manager.settings is not None
        await connection_manager.connect(websocket)
        try:
            await connection_manager.send_personal_message(
                self.stream_manager.get_initial_state_for_client(), websocket
            )
            await connection_manager.send_personal_message(
                {
                    "type": "update_stream_metadata",
                    **config_manager.settings.stream.model_dump(),
                },
                websocket,
            )

            initial_chats = get_recent_audience_chats(
                config_manager.settings.server.initial_chat_backlog_limit
            )
            for chat in initial_chats:
                await connection_manager.send_personal_message(
                    {"type": "chat_message", **chat, "is_user_message": False}, websocket
                )
                await asyncio.sleep(0.01)

            while True:
                raw_data = await websocket.receive_text()
                data = json.loads(raw_data)
                if data.get("type") == "user_message":
                    user_message = {
                        "username": data.get("username", "User"),
                        "text": data.get("text", "").strip(),
                    }
                    if user_message["text"]:
                        add_to_audience_buffer(user_message)
                        # Add to the new queue manager
                        logger.debug(f"Adding user message to queue: {user_message}")
                        self.stream_manager.queue_manager.add_chat(user_message)
                        await connection_manager.broadcast(
                            {
                                "type": "chat_message",
                                **user_message,
                                "is_user_message": True,
                            }
                        )
                elif data.get("type") == "superchat":  # This is now treated as a highlighted message
                    hl_message = {
                        "username": data.get("username", "User"),
                        "text": data.get("text", "").strip(),
                        "hl_type": data.get("sc_type", "bits"),  # Keep the original sc_type value
                    }
                    if hl_message["text"]:
                        # Add to the new queue manager as a highlight message
                        logger.debug(f"Adding highlighted message to queue: {hl_message}")
                        self.stream_manager.queue_manager.add_highlight_message(hl_message)

        except (WebSocketDisconnect, ConnectionResetError):
            pass
        finally:
            connection_manager.disconnect(websocket)