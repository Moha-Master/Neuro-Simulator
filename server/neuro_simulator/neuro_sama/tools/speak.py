"""The Speak tool for the agent."""

import logging
from typing import Dict, Any, List

from neuro_simulator.neuro_sama.tools.base import BaseTool
from neuro_simulator.neuro_sama import console

logger = logging.getLogger(__name__)


class SpeakTool(BaseTool):
    """Tool for the agent to speak to the audience."""

    def __init__(self):
        """Initializes the SpeakTool."""
        pass

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

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Executes the speak action.

        Args:
            **kwargs: Must contain a 'text' key with the string to be spoken.

        Returns:
            A dictionary confirming the action and the text that was spoken.
        """
        text = kwargs.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("The 'text' parameter must be a non-empty string.")

        logger.debug(f"Agent says: {text}")

        # Show console box for the speak action
        console.box_it_up(
            text.split('\n'),
            title="Neuro's Says",
            border_color=console.THEME["SPEAK"],
        )

        # The result of the speak tool is the text that was spoken.
        return {"status": "success", "spoken_text": text}