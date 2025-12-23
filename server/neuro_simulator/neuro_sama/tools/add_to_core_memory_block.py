"""The Add to Core Memory Block tool for the agent."""

from typing import Dict, Any, List

from neuro_simulator.neuro_sama.tools.base import BaseTool


class AddToCoreMemoryBlockTool(BaseTool):
    """Tool to add content to a specific core memory block."""

    def __init__(self, memory_manager):
        self.memory_manager = memory_manager

    @property
    def name(self) -> str:
        return "add_to_core_memory_block"

    @property
    def description(self) -> str:
        return "Adds content to a specific core memory block by its ID."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "block_id",
                "type": "string",
                "description": "The ID of the memory block to add content to.",
                "required": True,
            },
            {
                "name": "content",
                "type": "string",
                "description": "The content to add to the memory block.",
                "required": True,
            }
        ]

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        block_id = kwargs.get("block_id")
        content = kwargs.get("content")
        
        if not block_id:
            raise ValueError("The 'block_id' parameter is required.")
        if not content:
            raise ValueError("The 'content' parameter is required.")
        
        success = self.memory_manager.add_to_core_memory_block(block_id, content)

        if success:
            return {"status": "success", "message": f"Content added to block '{block_id}' successfully."}
        else:
            return {"status": "error", "message": f"Failed to add content to block '{block_id}'."}