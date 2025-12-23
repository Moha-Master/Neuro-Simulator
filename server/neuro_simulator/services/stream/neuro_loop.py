"""
Neuro loop module for the stream service.
Handles the Neuro agent's input/output cycle.
"""
import asyncio
import logging
import random
import time
from typing import Dict, Any, Optional, Callable

from ...utils.state import app_state
from ...core.config import ConfigManager
from ...core.agent_factory import create_agent
from ...utils.websocket import connection_manager
from .queue_manager import QueueManager

logger = logging.getLogger(__name__)

config_manager = ConfigManager()


class NeuroLoop:
    """Manages the Neuro agent's input/output cycle."""
    
    def __init__(self, 
                 queue_manager: QueueManager,
                 output_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.neuro_agent = None
        self.queue_manager = queue_manager
        self.output_callback = output_callback
        self.is_running = False
        self._neuro_busy = False
        self._tts_remaining_time = 0.0  # Tracks remaining TTS playback time
        self._last_update_time = 0.0  # Tracks when remaining time was last updated
        self._tasks = []
        self._has_sent_initial_greeting = False

    async def initialize(self):
        """Initialize the Neuro agent."""
        self.neuro_agent = await create_agent(output_callback=self._neuro_output_callback)
        if not self.neuro_agent:
            raise RuntimeError("Failed to create Neuro agent")

    def _neuro_output_callback(self, output_pack: Dict[str, Any]):
        """Internal callback to handle Neuro agent outputs."""
        import time

        # Update neuro_last_speech when Neuro speaks
        if output_pack.get("type") == "speak":
            payload = output_pack.get("payload", {})
            text = payload.get("text", "")
            if text:
                async def update_neuro_speech():
                    async with app_state.neuro_last_speech_lock:
                        app_state.neuro_last_speech = text
                # Run the update in the background
                asyncio.create_task(update_neuro_speech())

            # Add TTS duration to the remaining time
            if "duration" in payload:
                duration = payload["duration"]
                # Add a small buffer time to account for any processing delays
                buffer_time = 0.5
                total_duration = duration + buffer_time

                # Acquire a lock to ensure thread safety when updating the shared variable
                # Since this is called from async context, we'll update directly but ensure atomic updates
                current_time = time.time()
                # First, account for time that has passed since the last update
                time_passed = max(0, current_time - self._last_update_time)
                if self._last_update_time > 0:  # Only if we have a previous update
                    self._tts_remaining_time = max(0, self._tts_remaining_time - time_passed)

                # Add the new TTS duration
                self._tts_remaining_time += total_duration
                self._last_update_time = current_time

                # Mark as busy when we have TTS to play
                self._neuro_busy = True

        if self.output_callback:
            self.output_callback(output_pack)

    async def start(self):
        """Start the Neuro loop."""
        if self.is_running:
            return
            
        self.is_running = True
        logger.info("Starting Neuro loop")
        
        # Start the main processing task
        task = asyncio.create_task(self._neuro_response_cycle())
        self._tasks.append(task)

    async def stop(self):
        """Stop the Neuro loop."""
        self.is_running = False

        # Cancel all tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

        self._tasks.clear()

        # Reset state
        self._neuro_busy = False
        import time
        self._tts_remaining_time = 0.0  # Reset TTS remaining time
        self._last_update_time = time.time()  # Reset last update time

        logger.info("Stopped Neuro loop")

    async def _neuro_response_cycle(self):
        """Main Neuro response cycle."""
        logger.debug("Neuro response cycle started")
        
        while self.is_running:
            try:
                # Wait for live phase to begin
                if not app_state.live_phase_started_event.is_set():
                    logger.debug("Waiting for live phase to start...")
                    await app_state.live_phase_started_event.wait()
                    logger.debug("Live phase started, continuing...")
                
                # Update TTS remaining time based on elapsed time
                import time
                current_time = time.time()
                time_elapsed = max(0, current_time - self._last_update_time)
                self._tts_remaining_time = max(0, self._tts_remaining_time - time_elapsed)
                self._last_update_time = current_time  # Update the time tracker

                if self._tts_remaining_time > 0:
                    # Update busy flag based on remaining time
                    self._neuro_busy = True
                    logger.debug(f"Neuro is busy or TTS is still playing, remaining TTS time: {self._tts_remaining_time:.2f}s")
                    # Calculate remaining TTS time for more efficient sleep
                    sleep_time = min(0.5, max(0.01, self._tts_remaining_time))  # Sleep for remaining TTS time or 0.5s, whichever is smaller
                    await asyncio.sleep(sleep_time)
                    continue
                else:
                    # No more TTS remaining, ensure busy flag is cleared
                    self._neuro_busy = False
                    logger.debug("All TTS playback is complete")

                # At this point, all TTS playback is complete

                # Now wait for post-speech cooldown if needed
                if hasattr(config_manager, 'settings') and config_manager.settings:
                    post_speech_cooldown_range = config_manager.settings.neuro.post_speech_cooldown_sec
                    if isinstance(post_speech_cooldown_range, (list, tuple)) and len(post_speech_cooldown_range) >= 2:
                        min_cooldown, max_cooldown = post_speech_cooldown_range[0], post_speech_cooldown_range[1]
                        cooldown_time = random.uniform(min_cooldown, max_cooldown)
                        logger.debug(f"Waiting for post-speech cooldown: {cooldown_time:.2f}s")
                        await asyncio.sleep(cooldown_time)

                # Now get content from queue manager and reset TTS tracking for the new request
                logger.debug("Attempting to get content from queue manager")
                selected_chats = self.queue_manager.get_content()
                logger.debug(f"Retrieved {len(selected_chats)} chats from queue: {selected_chats}")

                # Reset TTS tracking for new request
                self._tts_remaining_time = 0.0
                self._last_update_time = time.time()

                # If this is the first interaction after live phase starts, add initial greeting
                if not self._has_sent_initial_greeting and selected_chats:
                    if config_manager.settings:
                        initial_greeting = config_manager.settings.neuro.initial_greeting
                        # Prepend initial greeting to the selected chats
                        selected_chats.insert(0, {"username": "System", "text": initial_greeting})
                        logger.debug(f"Added initial greeting to input: {initial_greeting}")
                        self._has_sent_initial_greeting = True
                    else:
                        # If no initial greeting is configured, just mark as sent
                        self._has_sent_initial_greeting = True

                # If no content from queues, use idle prompt
                if not selected_chats:
                    if config_manager.settings and hasattr(config_manager.settings.neuro, 'idle_prompt'):
                        idle_prompt = config_manager.settings.neuro.idle_prompt
                    else:
                        idle_prompt = "The chat seems quiet. Say something to engage with the audience."

                    selected_chats = [{"username": "System", "text": idle_prompt}]
                    logger.debug(f"Using idle prompt as input: {idle_prompt}")

                # Process the selected chats with the Neuro agent
                logger.debug(f"Processing chats with Neuro: {selected_chats}")
                await self.neuro_agent.process_and_respond(selected_chats)

                # Sleep briefly to prevent busy-waiting while waiting for responses
                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                logger.debug("Neuro response cycle was cancelled")
                break
            except Exception as e:
                logger.error(f"Error in Neuro response cycle: {e}", exc_info=True)
                await asyncio.sleep(5.0)  # Wait before retrying