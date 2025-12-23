"""The Delete Temp Memory Item tool for the agent."""

from typing import Dict, Any, List

from neuro_simulator.neuro_sama.tools.base import BaseTool


class DeleteTempMemoryItemTool(BaseTool):
    """Tool to delete an item from temporary memory by its ID."""

    def __init__(self, memory_manager):
        self.memory_manager = memory_manager

    @property
    def name(self) -> str:
        return "delete_temp_memory_item"

    @property
    def description(self) -> str:
        return "Deletes a specific temporary memory entry by its ID."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "item_id",
                "type": "string",
                "description": "The ID of the temporary memory item to delete.",
                "required": True,
            }
        ]

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        item_id = kwargs.get("item_id")
        
        if not item_id:
            raise ValueError("The 'item_id' parameter is required.")
        
        success = self.memory_manager.delete_temp_memory_item(item_id)

        if success:
            return {"status": "success", "message": f"Temporary memory item '{item_id}' deleted successfully."}
        else:
            return {"status": "error", "message": f"Failed to delete temporary memory item '{item_id}'."}