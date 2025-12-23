# neuro_simulator/agent/tools/model_zoom.py
import logging
from typing import Dict, Any, List

from neuro_simulator.agents.tools.base import BaseTool
from neuro_simulator.utils import console

logger = logging.getLogger(__name__)


class ModelZoomTool(BaseTool):
    """A tool to make the client-side avatar zoom in."""

    def __init__(self, memory_manager=None, output_manager=None):
        # Accept memory_manager and output_manager for consistency with other tools
        self.output_manager = output_manager

    @property
    def name(self) -> str:
        return "model_zoom"

    @property
    def description(self) -> str:
        return "Makes model zoom in, just like got closer to fans."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return []

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Sends a model zoom command through the output manager.
        """
        logger.debug(f"Executing {self.name} tool.")
        try:
            # Send model action through output manager
            if self.output_manager:
                await self.output_manager.send_model_output("zoom")

            console.box_it_up(
                ["Command: model_zoom"],
                title="Executed Model Tool",
                border_color=console.THEME["TOOL"],
            )
            return {"status": "success", "message": "Zoom command sent."}
        except Exception as e:
            logger.error(f"Error in {self.name} tool: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}
