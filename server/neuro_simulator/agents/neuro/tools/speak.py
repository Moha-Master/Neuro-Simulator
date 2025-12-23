# neuro_simulator/agent/tools/speak.py
"""The Speak tool for the agent with integrated TTS."""

import asyncio
import logging
from typing import Dict, Any, List

from neuro_simulator.agents.tools.base import BaseTool
from neuro_simulator.agents.memory.manager import MemoryManager
from neuro_simulator.utils import console
from neuro_simulator.core.config import config_manager
from neuro_simulator.services.audio import synthesize_audio_segment

logger = logging.getLogger(__name__)


class SpeakTool(BaseTool):
    """Tool for the agent to speak to the audience."""

    def __init__(self, memory_manager: MemoryManager, output_manager=None):
        """Initializes the SpeakTool."""
        # This tool doesn't directly use the memory manager, but accepts it for consistency.
        self.memory_manager = memory_manager
        self.output_manager = output_manager

    @property
    def name(self) -> str:
        return "speak"

    @property
    def description(self) -> str:
        return "Outputs text to the user. This is the primary way to communicate with the audience."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "text",
                "type": "string",
                "description": "The text to be spoken to the audience.",
                "required": True,
            }
        ]

    def _apply_keyword_filter(self, text: str) -> str:
        """
        Applies keyword filtering to the text if keywords are configured.

        Args:
            text: The input text to filter

        Returns:
            The filtered text, or "Filtered." if a keyword is found
        """
        if not config_manager.settings:
            return text

        neuro_settings = config_manager.settings.neuro

        # Only apply filtering if there are keywords configured
        if not neuro_settings.filter_keywords:
            return text

        # Convert text to lowercase for case-insensitive matching
        lower_text = text.lower()

        # Check if any filter keyword is in the text
        for keyword in neuro_settings.filter_keywords:
            if keyword.lower() in lower_text:
                logger.info(f"Keyword '{keyword}' found in text. Filtering message.")
                return "Filtered."

        return text

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Executes the speak action with integrated TTS.

        Args:
            **kwargs: Must contain a 'text' key with the string to be spoken.

        Returns:
            A dictionary confirming the action and the text that was spoken.
        """
        text = kwargs.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("The 'text' parameter must be a non-empty string.")

        # Apply keyword filtering if enabled
        filtered_text = self._apply_keyword_filter(text)

        console.box_it_up(
            filtered_text.split('\n'),
            title="Neuro's Speaks",
            border_color=console.THEME["SPEAK"],
        )

        logger.debug(f"Agent says: {filtered_text}")

        # If filtered, just return without TTS
        if filtered_text == "Filtered.":
            return {"status": "success", "spoken_text": filtered_text}

        # Get TTS provider ID from config
        tts_provider_id = config_manager.settings.neuro.tts_provider_id
        if not tts_provider_id:
            logger.warning("TTS Provider ID is not set. Only logging the text without audio synthesis.")
            # Send text-only output
            if self.output_manager:
                await self.output_manager.send_speak_output(text=filtered_text)
            return {"status": "success", "spoken_text": filtered_text}

        # Synthesize audio for the text
        try:
            synthesis_result = await synthesize_audio_segment(
                filtered_text, tts_provider_id=tts_provider_id
            )

            # Handle TTS timeout
            if (
                isinstance(synthesis_result, tuple)
                and synthesis_result[0] == "timeout"
            ):
                logger.warning(
                    "TTS synthesis timed out."
                )
                # Send text-only output as fallback
                if self.output_manager:
                    await self.output_manager.send_speak_output(text=filtered_text)
                return {"status": "success", "spoken_text": filtered_text}

            # Handle other synthesis errors
            if isinstance(synthesis_result, Exception):
                raise synthesis_result

            # Send speak output through output manager
            if self.output_manager:
                audio_base64, duration = synthesis_result
                await self.output_manager.send_speak_output(
                    text=filtered_text,
                    audio_base64=audio_base64,
                    duration=duration
                )

        except Exception as e:
            logger.error(f"TTS synthesis failed: {e}")
            # Send text-only output as fallback
            if self.output_manager:
                await self.output_manager.send_speak_output(text=filtered_text)
            return {"status": "success", "spoken_text": filtered_text}

        # The result of the speak tool is the text that was spoken.
        # This can be used for logging or further processing.
        return {"status": "success", "spoken_text": filtered_text}
