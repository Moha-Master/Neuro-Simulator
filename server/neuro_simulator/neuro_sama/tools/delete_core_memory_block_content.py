"""The Delete Core Memory Block Content tool for the agent."""

from typing import Dict, Any, List

from neuro_simulator.neuro_sama.tools.base import BaseTool


class DeleteCoreMemoryBlockContentTool(BaseTool):
    """Tool to delete content from a specific core memory block."""

    def __init__(self, memory_manager):
        self.memory_manager = memory_manager

    @property
    def name(self) -> str:
        return "delete_core_memory_block_content"

    @property
    def description(self) -> str:
        return "Deletes specific content from a core memory block by its ID."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "block_id",
                "type": "string",
                "description": "The ID of the memory block to delete content from.",
                "required": True,
            },
            {
                "name": "content",
                "type": "string",
                "description": "The content to delete from the memory block.",
                "required": True,
            }
        ]

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        block_id = kwargs.get("block_id")
        content = kwargs.get("content")
        
        if not block_id:
            raise ValueError("The 'block_id' parameter is required.")
        if content is None:
            raise ValueError("The 'content' parameter is required.")
        
        success = self.memory_manager.remove_from_core_memory_block(block_id, content)

        if success:
            return {"status": "success", "message": f"Content removed from block '{block_id}' successfully."}
        else:
            return {"status": "error", "message": f"Failed to remove content from block '{block_id}'."}