"""
Stream manager module for the stream service.
Coordinates the entire streaming process and manages stream state.
"""
import asyncio
import logging
from typing import Dict, Any, Optional, Callable
from datetime import datetime
from collections import deque

from ...utils.state import app_state
from ...core.config import config_manager
from ...utils.websocket import connection_manager
from .neuro_loop import NeuroLoop
from .chatbot_loop import ChatbotLoop
from .queue_manager import QueueManager

logger = logging.getLogger(__name__)


class StreamManager:
    """Manages the entire streaming process, coordinating between agents and clients."""

    def __init__(self):
        self.queue_manager = QueueManager()
        self.neuro_loop = None
        self.chatbot_loop = None
        self.is_running = False
        self._tasks = []

        # State management attributes
        self.event_queue = asyncio.Queue()
        self._stream_start_time = None
        self._current_phase = "offline"
        self._welcome_video_progress = 0.0
        self._neuro_avatar_stage = "hidden"
        self._is_neuro_speaking = False
        self._elapsed_time_since_stream_start = 0.0

    def _kill_existing_processes(self):
        """Placeholder for killing any existing processes that might interfere."""
        # Currently no specific processes need to be terminated for this application
        # This function can be expanded if specific cleanup is needed in the future
        pass  # No-op for now

    def reset_stream_state(self):
        """Reset the stream state to initial values."""
        self._stream_start_time = datetime.now()
        self._current_phase = "initializing"
        self._welcome_video_progress = 0.0
        self._neuro_avatar_stage = "hidden"
        self._is_neuro_speaking = False
        self._elapsed_time_since_stream_start = 0.0

        logger.info("Stream state has been reset")

    def get_initial_state_for_client(self) -> Dict[str, Any]:
        """Get the initial state to send to a new client."""
        if config_manager.settings is None:
            logger.warning("Config not loaded yet, returning minimal state")
            return {
                "type": "stream_state_sync",
                "state": {
                    "current_phase": self._current_phase,
                    "welcome_video_progress": self._welcome_video_progress,
                    "neuro_avatar_stage": self._neuro_avatar_stage,
                    "is_neuro_speaking": self._is_neuro_speaking,
                    "elapsed_time_since_stream_start": self._elapsed_time_since_stream_start,
                }
            }

        return {
            "type": "stream_state_sync",
            "state": {
                "current_phase": self._current_phase,
                "welcome_video_progress": self._welcome_video_progress,
                "neuro_avatar_stage": self._neuro_avatar_stage,
                "is_neuro_speaking": self._is_neuro_speaking,
                "elapsed_time_since_stream_start": self._elapsed_time_since_stream_start,
            }
        }

    async def broadcast_stream_metadata(self):
        """Broadcast stream metadata to all clients."""
        if config_manager.settings is None:
            logger.warning("Config not loaded, cannot broadcast stream metadata")
            return

        metadata = {
            "type": "update_stream_metadata",
            **config_manager.settings.stream.model_dump(),
        }

        # Add to event queue to be broadcast by the background task
        await self.event_queue.put(metadata)
        logger.debug("Stream metadata broadcast queued")

    def get_status(self) -> Dict[str, Any]:
        """Get the current stream status."""
        return {
            "is_running": self.is_running,
            "backend_status": "running" if self.is_running else "stopped",
        }

    def _neuro_output_callback(self, output_pack: Dict[str, Any]):
        """Callback to handle Neuro agent outputs."""
        # Forward output to clients
        asyncio.create_task(connection_manager.broadcast(output_pack))

    def _chatbot_output_callback(self, output_pack: Dict[str, Any]):
        """Callback to handle Chatbot agent outputs."""
        # Forward output to clients
        asyncio.create_task(connection_manager.broadcast(output_pack))

        # If this is a chat message from the Chatbot, add it to the queue for Neuro to see
        if output_pack.get("type") == "chat_message":
            payload = output_pack.get("payload", {})
            text = payload.get("text", "")
            username = payload.get("username", "Chatbot")
            if text:
                # Add the Chatbot's message to the queue so Neuro can see it
                self.queue_manager.add_chat({"username": username, "text": text})

    async def start_stream(self):
        """Start the streaming process."""
        logger.info("Starting stream...")
        
        if self.is_running:
            logger.warning("Stream is already running")
            return

        # Kill any existing processes
        self._kill_existing_processes()

        # Initialize queues
        self.queue_manager.clear_chat_queue()
        self.queue_manager.clear_highlight_queue()
        self.queue_manager.clear_highlighted_message_queue()

        # Reset stream state
        self.reset_stream_state()

        # Reset the live phase started event for this stream cycle
        app_state.live_phase_started_event.clear()
        
        # Set an initial value for neuro_last_speech to provide context for Chatbot
        async with app_state.neuro_last_speech_lock:
            app_state.neuro_last_speech = "Neuro is preparing to start the stream. The chat is currently welcoming viewers."

        # Create agent loops
        self.chatbot_loop = ChatbotLoop(output_callback=self._chatbot_output_callback)
        self.neuro_loop = NeuroLoop(self.queue_manager, output_callback=self._neuro_output_callback)

        # Initialize agents
        await self.chatbot_loop.initialize()
        await self.neuro_loop.initialize()

        # Reset agent memories
        await self.neuro_loop.neuro_agent.reset_memory()
        if self.chatbot_loop.chatbot:
            await self.chatbot_loop.chatbot.reset_memory()

        # Set the running flag
        self.is_running = True

        # Start the agent loops
        await self.chatbot_loop.start()
        await self.neuro_loop.start()

        # Send stream phase transition messages to clients to indicate live phase
        # First, broadcast welcome video start (duration based on actual video file)
        import os
        from pathlib import Path

        # Define the path to the welcome video file
        _WELCOME_VIDEO_PATH_BACKEND = Path(__file__).parent.parent.parent / "assets" / "neuro_start.mp4"

        def _get_video_duration(video_path: Path) -> float:
            """Get the duration of a video file in seconds."""
            # Check if file exists first
            if not video_path.exists():
                logger.warning(f"Video file does not exist: {video_path}")
                return 10.0  # Default duration if file is missing

            try:
                # Use mutagen to get the duration of the MP4 file
                from mutagen.mp4 import MP4
                audio_file = MP4(str(video_path))
                if audio_file is not None and audio_file.info is not None:
                    duration = audio_file.info.length
                    return duration
                else:
                    logger.warning(f"Could not read duration from {video_path}")
                    return 10.0  # Default duration
            except Exception as e:
                logger.warning(f"Error reading video duration with mutagen: {e}")
                return 10.0  # Default duration on error

        welcome_video_duration = _get_video_duration(_WELCOME_VIDEO_PATH_BACKEND)
        logger.debug(f"Sending phase transition messages with welcome video duration: {welcome_video_duration}s")

        await connection_manager.broadcast({
            "type": "play_welcome_video",
            "progress": 0,
            "elapsed_time_sec": 0
        })
        logger.debug("Sent play_welcome_video message")
        await asyncio.sleep(welcome_video_duration)  # Wait for welcome video duration

        await connection_manager.broadcast({
            "type": "start_avatar_intro",
            "elapsed_time_sec": welcome_video_duration
        })
        logger.debug(f"Sent start_avatar_intro message after {welcome_video_duration}s")
        avatar_intro_duration = 3.0  # Standard avatar intro duration
        await asyncio.sleep(avatar_intro_duration)  # Wait for avatar intro duration

        await connection_manager.broadcast({
            "type": "enter_live_phase",
            "elapsed_time_sec": welcome_video_duration + avatar_intro_duration
        })
        logger.debug(f"Sent enter_live_phase message after total delay of {welcome_video_duration + avatar_intro_duration}s")

        # Set the live phase started event to allow Neuro to begin processing
        app_state.live_phase_started_event.set()
        logger.debug("Set live phase started event")

        logger.info("Stream started successfully.")

        # Broadcast stream status update
        status = self.get_status()
        await connection_manager.broadcast({"type": "stream_status_update", **status})

    async def stop_stream(self):
        """Stop the streaming process."""
        logger.info("Stopping stream...")
        
        if not self.is_running:
            logger.warning("Stream is not running")
            return

        self.is_running = False

        # Stop the agent loops
        if self.neuro_loop:
            await self.neuro_loop.stop()
        if self.chatbot_loop:
            await self.chatbot_loop.stop()

        # Cancel any remaining tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()

        # Broadcast stream status update
        status = {
            "is_running": self.is_running,
            "backend_status": "running" if self.is_running else "stopped",
        }
        await connection_manager.broadcast({"type": "stream_status_update", **status})

        logger.info("Stream stopped successfully.")

    def add_chat(self, chat: Dict[str, str]) -> None:
        """Add a chat message to the queue."""
        self.queue_manager.add_chat(chat)

    def add_highlight_message(self, highlight: Dict[str, str]) -> None:
        """Add a highlight message to the queue."""
        self.queue_manager.add_highlight_message(highlight)

    def add_highlighted_message(self, highlighted_message: Dict[str, Any]) -> None:
        """Add a highlighted message to the queue."""
        self.queue_manager.add_highlighted_message(highlighted_message)