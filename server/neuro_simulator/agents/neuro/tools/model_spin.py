# neuro_simulator/agent/tools/model_spin.py
import logging
from typing import Dict, Any, List

from neuro_simulator.agents.tools.base import BaseTool
from neuro_simulator.utils import console

logger = logging.getLogger(__name__)


class ModelSpinTool(BaseTool):
    """A tool to make the client-side avatar spin."""

    def __init__(self, memory_manager=None, output_manager=None):
        # Accept memory_manager and output_manager for consistency with other tools
        self.output_manager = output_manager

    @property
    def name(self) -> str:
        return "model_spin"

    @property
    def description(self) -> str:
        return "Makes model spin once, dont got too dizzy when spining lol."

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        return []

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Sends a model spin command through the output manager.
        """
        logger.debug(f"Executing {self.name} tool.")
        try:
            # Send model action through output manager
            if self.output_manager:
                await self.output_manager.send_model_output("spin")

            console.box_it_up(
                ["Command: model_spin"],
                title="Executed Model Tool",
                border_color=console.THEME["TOOL"],
            )
            return {"status": "success", "message": "Spin command sent."}
        except Exception as e:
            logger.error(f"Error in {self.name} tool: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}
