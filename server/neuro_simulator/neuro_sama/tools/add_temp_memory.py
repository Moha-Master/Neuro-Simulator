"""The Add Temp Memory tool for the agent."""

import logging
from typing import Dict, Any, List

from neuro_simulator.neuro_sama.tools.base import BaseTool

logger = logging.getLogger(__name__)


class AddTempMemoryTool(BaseTool):
    """Tool for the agent to add temporary memory."""

    def __init__(self, memory_manager):
        self.memory_manager = memory_manager

    @property
    def name(self) -> str:
        return "add_temp_memory"

    @property
    def description(self) -> str:
        return "Adds a temporary memory entry. Use this to remember things for the current session."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "content",
                "type": "string",
                "description": "The content to store in temporary memory.",
                "required": True,
            },
            {
                "name": "role",
                "type": "string",
                "description": "The role associated with this memory entry (e.g., 'assistant', 'user', 'system').",
                "required": False,
            }
        ]

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Executes the add_temp_memory action.
        """
        content = kwargs.get("content")
        role = kwargs.get("role", "assistant")  # Default to assistant if not provided
        
        if not isinstance(content, str) or not content:
            raise ValueError("The 'content' parameter must be a non-empty string.")

        success = self.memory_manager.add_temp_memory(content, role)
        
        if success:
            return {"status": "success", "message": "Temporary memory added successfully."}
        else:
            return {"status": "error", "message": "Failed to add temporary memory."}