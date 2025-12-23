"""
Chatbot loop module for the stream service.
Handles the Chatbot agent's input/output cycle.
"""
import asyncio
import logging
from typing import Dict, Any, Optional, Callable
import time

from ...utils.state import app_state
from ...core.config import ConfigManager
from ...core.chatbot_factory import create_chatbot
from ...utils.websocket import connection_manager

logger = logging.getLogger(__name__)

config_manager = ConfigManager()


class ChatbotLoop:
    """Manages the Chatbot agent's input/output cycle."""
    
    def __init__(self, output_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.chatbot = None
        self.output_callback = output_callback
        self.is_running = False
        self._tasks = []
        self._neuro_last_speech = ""

    async def initialize(self):
        """Initialize the Chatbot agent."""
        self.chatbot = await create_chatbot(output_callback=self._chatbot_output_callback)
        if not self.chatbot:
            raise RuntimeError("Failed to create Chatbot agent")
        
        # Set initial neuro_last_speech value
        async with app_state.neuro_last_speech_lock:
            self._neuro_last_speech = app_state.neuro_last_speech or "Neuro has not spoken yet."

    def _chatbot_output_callback(self, output_pack: Dict[str, Any]):
        """Internal callback to handle Chatbot agent outputs."""
        if self.output_callback:
            self.output_callback(output_pack)

    async def start(self):
        """Start the Chatbot loop."""
        if self.is_running:
            return
            
        self.is_running = True
        logger.info("Starting Chatbot loop")
        
        # Start the main processing task
        task = asyncio.create_task(self._chatbot_cycle())
        self._tasks.append(task)

    async def stop(self):
        """Stop the Chatbot loop."""
        self.is_running = False
        
        # Cancel all tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
                
        self._tasks.clear()
        logger.info("Stopped Chatbot loop")

    async def _chatbot_cycle(self):
        """Main Chatbot processing loop."""
        logger.debug("Chatbot cycle started")
        
        while self.is_running:
            try:
                # Update neuro_last_speech from app_state
                async with app_state.neuro_last_speech_lock:
                    current_neuro_speech = app_state.neuro_last_speech or self._neuro_last_speech
                
                # Generate chat messages with the latest neuro speech as context
                recent_history = await self.chatbot.get_message_history(limit=5)  # Get recent history
                await self.chatbot.generate_chat_messages(neuro_speech=current_neuro_speech, recent_history=recent_history)
                
                # Sleep briefly to prevent busy-waiting
                await asyncio.sleep(1.0)
                
            except asyncio.CancelledError:
                logger.debug("Chatbot cycle was cancelled")
                break
            except Exception as e:
                logger.error(f"Error in Chatbot cycle: {e}", exc_info=True)
                await asyncio.sleep(5.0)  # Wait before retrying