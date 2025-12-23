"""Tool manager for the Neuro Sama module."""

import importlib
import os
from typing import Dict, Any, List
from .tools.base import BaseTool


class ToolManager:
    """Manages the loading and execution of tools."""

    def __init__(self, tools_dir: str = None, memory_manager=None):
        self.tools_dir = tools_dir or os.path.join(os.path.dirname(__file__), "tools")
        self.memory_manager = memory_manager
        self.tools: Dict[str, BaseTool] = {}
        self.load_tools()

    def load_tools(self):
        """Dynamically load all tools from the tools directory."""
        for filename in os.listdir(self.tools_dir):
            if filename.endswith(".py") and not filename.startswith("__"):
                module_name = filename[:-3]  # Remove .py extension

                # Import the module
                spec = importlib.util.spec_from_file_location(
                    module_name,
                    os.path.join(self.tools_dir, filename)
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Find the tool class in the module
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, BaseTool)
                        and attr != BaseTool
                    ):
                        # Create an instance of the tool, passing memory_manager if needed
                        if 'memory_manager' in attr.__init__.__code__.co_varnames:
                            tool_instance = attr(memory_manager=self.memory_manager)
                        else:
                            tool_instance = attr()
                        self.tools[tool_instance.name] = tool_instance
                        print(f"Loaded tool: {tool_instance.name}")

    def get_tool(self, name: str) -> BaseTool:
        """Get a tool by name."""
        return self.tools.get(name)

    def get_all_tools(self) -> Dict[str, BaseTool]:
        """Get all tools."""
        return self.tools

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get all tool schemas for the LLM."""
        schemas = []
        for tool in self.tools.values():
            schema = {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters
            }
            schemas.append(schema)
        return schemas