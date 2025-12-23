"""
Queue management module for the stream service.
Handles chat and highlight message queues with priority-based retrieval.
"""
import asyncio
import random
from collections import deque
from typing import List, Dict, Any, Optional, Deque
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import time

import logging
from ...core.config import ConfigManager
from ...utils.state import app_state

logger = logging.getLogger(__name__)

config_manager = ConfigManager()


class QueueManager:
    """Manages chat and highlight message queues with priority-based retrieval."""
    
    def __init__(self):
        self._chat_queue: Deque[Dict[str, str]] = deque()
        self._highlight_queue: Deque[Dict[str, str]] = deque()
        self._chat_lock = Lock()
        self._highlight_lock = Lock()
        
        # For highlighted message handling
        self._highlighted_message_queue: Deque[Dict[str, Any]] = deque()
        self._highlighted_message_lock = Lock()
        self._last_highlighted_message_time = 0.0

    def add_chat(self, chat: Dict[str, str]) -> None:
        """Add a regular chat message to the queue."""
        with self._chat_lock:
            self._chat_queue.append(chat)
            logger.debug(f"Added chat to queue: {chat}, queue size now: {len(self._chat_queue)}")

    def add_highlight_message(self, highlight: Dict[str, str]) -> None:
        """Add a highlight message to the queue (higher priority)."""
        with self._highlight_lock:
            self._highlight_queue.append(highlight)

    def add_highlighted_message(self, highlighted_message: Dict[str, Any]) -> None:
        """Add a highlighted message to the queue."""
        with self._highlighted_message_lock:
            self._highlighted_message_queue.append(highlighted_message)

    def get_content(self, sample_size: Optional[int] = None) -> List[Dict[str, str]]:
        """
        Retrieve content from queues with priority logic.
        If highlight queue has content, return one highlight message.
        Otherwise, return up to sample_size chat messages.
        """
        logger.debug(f"Getting content from queues, chat queue size: {len(self._chat_queue)}")

        # Check for highlighted messages first (highest priority)
        with self._highlighted_message_lock:
            if (self._highlighted_message_queue and
                (time.time() - self._last_highlighted_message_time > 10)):
                hl = self._highlighted_message_queue.popleft()
                self._last_highlighted_message_time = time.time()
                logger.debug(f"Retrieved highlighted message: {hl}")
                return [{"username": hl["username"], "text": hl["text"]}]

        # Check for highlight messages
        with self._highlight_lock:
            if self._highlight_queue:
                highlight = self._highlight_queue.popleft()
                logger.debug(f"Retrieved highlight message: {highlight}")
                return [highlight]

        # Otherwise, get regular chat messages
        with self._chat_lock:
            available_chats = list(self._chat_queue)
            logger.debug(f"Available chats in queue: {len(available_chats)}")

            if not available_chats:
                logger.debug("No chats available in queue")
                return []

            if sample_size is None:
                # Check if config is loaded
                if config_manager.settings is None:
                    logger.warning("Config not loaded yet, using default sample size")
                    sample_size = min(10, len(available_chats))  # Default to 10 if config not available
                else:
                    sample_size = min(
                        config_manager.settings.neuro.input_chat_sample_size,
                        len(available_chats)
                    )

            sample_size = min(sample_size, len(available_chats))
            logger.debug(f"Selecting {sample_size} chats from {len(available_chats)} available")

            selected_chats = random.sample(available_chats, sample_size)

            # Remove selected chats from the queue
            for chat in selected_chats:
                try:
                    self._chat_queue.remove(chat)
                except ValueError:
                    # Chat may have been removed by another thread
                    continue

            logger.debug(f"Selected chats: {selected_chats}")
            return selected_chats

    def clear_chat_queue(self) -> None:
        """Clear the regular chat queue."""
        with self._chat_lock:
            self._chat_queue.clear()

    def clear_highlight_queue(self) -> None:
        """Clear the highlight message queue."""
        with self._highlight_lock:
            self._highlight_queue.clear()

    def clear_highlighted_message_queue(self) -> None:
        """Clear the highlighted message queue."""
        with self._highlighted_message_lock:
            self._highlighted_message_queue.clear()

    def get_chat_queue_size(self) -> int:
        """Get the current size of the chat queue."""
        with self._chat_lock:
            return len(self._chat_queue)

    def get_highlight_queue_size(self) -> int:
        """Get the current size of the highlight queue."""
        with self._highlight_lock:
            return len(self._highlight_queue)

    def get_highlighted_message_queue_size(self) -> int:
        """Get the current size of the highlighted message queue."""
        with self._highlighted_message_lock:
            return len(self._highlighted_message_queue)