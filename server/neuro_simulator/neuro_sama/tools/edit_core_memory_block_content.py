"""The Edit Core Memory Block Content tool for the agent."""

from typing import Dict, Any, List

from neuro_simulator.neuro_sama.tools.base import BaseTool


class EditCoreMemoryBlockContentTool(BaseTool):
    """Tool to edit content in a specific core memory block."""

    def __init__(self, memory_manager):
        self.memory_manager = memory_manager

    @property
    def name(self) -> str:
        return "edit_core_memory_block_content"

    @property
    def description(self) -> str:
        return "Edits content in a specific core memory block by replacing old content with new content."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "block_id",
                "type": "string",
                "description": "The ID of the memory block to edit content in.",
                "required": True,
            },
            {
                "name": "old_content",
                "type": "string",
                "description": "The content to be replaced.",
                "required": True,
            },
            {
                "name": "new_content",
                "type": "string",
                "description": "The new content to replace the old content with.",
                "required": True,
            }
        ]

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        block_id = kwargs.get("block_id")
        old_content = kwargs.get("old_content")
        new_content = kwargs.get("new_content")
        
        if not block_id:
            raise ValueError("The 'block_id' parameter is required.")
        if old_content is None:
            raise ValueError("The 'old_content' parameter is required.")
        if new_content is None:
            raise ValueError("The 'new_content' parameter is required.")
        
        # Get the current block
        block = self.memory_manager.get_core_memory_block(block_id)
        if not block:
            return {"status": "error", "message": f"Block '{block_id}' not found."}
        
        content_list = block.get('content', [])
        
        if old_content in content_list:
            # Replace the old content with new content
            idx = content_list.index(old_content)
            content_list[idx] = new_content
            
            # Update the block
            success = self.memory_manager.update_core_memory_block(
                block_id, content=content_list
            )
            
            if success:
                return {"status": "success", "message": f"Content in block '{block_id}' updated successfully."}
            else:
                return {"status": "error", "message": f"Failed to update content in block '{block_id}'."}
        else:
            return {"status": "error", "message": f"Content '{old_content}' not found in block '{block_id}'."}