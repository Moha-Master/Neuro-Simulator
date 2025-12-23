# neuro_simulator/chatbot/tools/post_chat_message.py
"""The Post Chat Message tool for the chatbot agent."""

import logging
from typing import Dict, Any, List

from neuro_simulator.agents.tools.base import BaseTool

logger = logging.getLogger(__name__)


class PostChatMessageTool(BaseTool):
    """Tool for the chatbot to post a message to the stream chat."""

    def __init__(self, memory_manager=None, output_manager=None):
        super().__init__(memory_manager)
        self.output_manager = output_manager

    @property
    def name(self) -> str:
        return "post_chat_message"

    @property
    def description(self) -> str:
        return "Posts a text message to the stream chat, as if you are a viewer."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "text",
                "type": "string",
                "description": "The content of the chat message to post.",
                "required": True,
            }
        ]

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Executes the action. This tool sends the message through the output manager.
        """
        text = kwargs.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("The 'text' parameter must be a non-empty string.")

        # Send chat message through output manager
        if self.output_manager:
            await self.output_manager.send_custom_output(
                output_type="chat_message",
                payload={
                    "text": text
                }
            )

        # The result is the text to be posted.
        return {"status": "success", "text_to_post": text}
