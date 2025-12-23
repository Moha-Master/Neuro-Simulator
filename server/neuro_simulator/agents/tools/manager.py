# neuro_simulator/agents/tools/manager.py
"""The central tool manager for all agents, responsible for loading, managing, and executing tools."""

import os
import json
import importlib
import inspect
import logging
from pathlib import Path
from typing import Any, Dict, List

from .base import BaseTool
from ..memory.manager import MemoryManager

logger = logging.getLogger(__name__)


class ToolManager:
    """
    Acts as a central registry and executor for all available tools for a given agent.
    """

    def __init__(
        self,
        memory_manager: MemoryManager,
        output_manager=None,
        builtin_tools_path: Path = None,
        user_tools_path: Path = None,
        tool_allocations_paths: Dict[str, Path] = None,
        default_allocations: Dict[str, List[str]] = None
    ):
        self.memory_manager = memory_manager
        self.output_manager = output_manager
        self.builtin_tools_path = builtin_tools_path or Path()
        self.user_tools_path = user_tools_path or Path()
        self.tool_allocations_paths = tool_allocations_paths or {}
        self.default_allocations = default_allocations or {}

        self.tools: Dict[str, BaseTool] = {}
        self.agent_tool_allocations: Dict[str, List[str]] = {}

    def load_tools(self):
        """Dynamically scans tool directories, imports modules, and registers tool instances."""
        logger.debug(f"Loading tools for agent from: {self.builtin_tools_path.parent.name}")
        self.tools = {}
        tool_paths = [self.builtin_tools_path, self.user_tools_path]

        for tools_dir in tool_paths:
            if not tools_dir.exists():
                continue

            logger.debug(f"Scanning for tools in: {tools_dir}")
            for filename in os.listdir(tools_dir):
                if filename.endswith(".py") and not filename.startswith(
                    ("__", "base", "manager")
                ):
                    module_path = tools_dir / filename
                    spec = importlib.util.spec_from_file_location(
                        f"neuro_simulator.agents.tools.{module_path.stem}", module_path
                    )
                    if spec and spec.loader:
                        try:
                            module = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(module)
                            for _, cls in inspect.getmembers(module, inspect.isclass):
                                if (
                                    issubclass(cls, BaseTool)
                                    and cls is not BaseTool
                                ):
                                    # Try to instantiate with both memory_manager and output_manager
                                    try:
                                        tool_instance = cls(
                                            memory_manager=self.memory_manager,
                                            output_manager=self.output_manager
                                        )
                                    except TypeError:
                                        # If the tool doesn't accept output_manager, try with just memory_manager
                                        try:
                                            tool_instance = cls(
                                                memory_manager=self.memory_manager
                                            )
                                        except TypeError:
                                            # If the tool doesn't accept memory_manager either, try without parameters
                                            tool_instance = cls()

                                    if tool_instance.name in self.tools:
                                        logger.warning(
                                            f"Duplicate tool name '{tool_instance.name}' found. Overwriting with version from {module_path}."
                                        )
                                    self.tools[tool_instance.name] = tool_instance
                                    logger.debug(
                                        f"Successfully loaded tool: {tool_instance.name}"
                                    )
                        except Exception as e:
                            logger.error(
                                f"Failed to load tool from {module_path}: {e}",
                                exc_info=True,
                            )
        self._load_allocations()

    def _load_allocations(self):
        """Loads tool allocations from JSON files, creating defaults if they don't exist."""
        self.agent_tool_allocations = {}
        for agent_name, file_path in self.tool_allocations_paths.items():
            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    self.agent_tool_allocations[agent_name] = json.load(f)
            else:
                default = self.default_allocations.get(agent_name, [])
                self.agent_tool_allocations[agent_name] = default
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(default, f, indent=2)

        logger.debug(f"Tool allocations loaded: {self.agent_tool_allocations}")

    def get_all_tool_schemas(self) -> List[Dict[str, Any]]:
        return [tool.get_schema() for tool in self.tools.values()]

    def get_tool_schemas_for_agent(self, agent_name: str) -> List[Dict[str, Any]]:
        allowed_names = set(self.agent_tool_allocations.get(agent_name, []))
        if not allowed_names:
            return []
        return [
            tool.get_schema()
            for tool in self.tools.values()
            if tool.name in allowed_names
        ]

    def reload_tools(self):
        logger.info("Reloading tools...")
        self.load_tools()
        logger.info(f"Tools reloaded. {len(self.tools)} tools available.")

    def get_allocations(self) -> Dict[str, List[str]]:
        return self.agent_tool_allocations

    def set_allocations(self, allocations: Dict[str, List[str]]):
        self.agent_tool_allocations = allocations
        # Persist the changes to the individual JSON files
        for agent_name, file_path in self.tool_allocations_paths.items():
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(allocations.get(agent_name, []), f, indent=2)
        logger.info(
            f"Tool allocations updated and saved: {self.agent_tool_allocations}"
        )

    async def execute_tool(self, tool_name: str, **kwargs: Any) -> Dict[str, Any]:
        if tool_name not in self.tools:
            logger.error(f"Attempted to execute non-existent tool: {tool_name}")
            return {"error": f"Tool '{tool_name}' not found."}
        tool = self.tools[tool_name]
        try:
            result = await tool.execute(**kwargs)
            return result
        except Exception as e:
            logger.error(
                f"Error executing tool '{tool_name}' with params {kwargs}: {e}",
                exc_info=True,
            )
            return {
                "error": f"An unexpected error occurred while executing the tool: {str(e)}"
            }
