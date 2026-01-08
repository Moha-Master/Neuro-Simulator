"""Context builder for the Neuro Sama module."""

import json
import os
from typing import Dict, Any, List

from .tool_manager import ToolManager
from .memory_manager import MemoryManager


class ContextBuilder:
    """Builds dynamic context for the LLM based on memory and input."""

    def __init__(self, config, on_memory_change_callback=None):
        self.config = config
        self.memory_manager = MemoryManager(config, on_memory_change_callback)
        self.tool_manager = ToolManager(memory_manager=self.memory_manager)

    def format_core_memory(self) -> str:
        """Format core memory for the prompt."""
        core_memory = self.memory_manager.get_core_memory_blocks()
        if not core_memory:
            return "Not set."

        formatted_parts = []
        for block_id, block in core_memory.items():
            title = block.get('title', '')
            description = block.get('description', '')
            content = block.get('content', [])

            formatted_parts.append(
                f"\nBlock: {title} ({block_id})\nDescription: {description}\nContent:\n" +
                "\n".join([f"  - {item}" for item in content])
            )

        return "\n".join(formatted_parts) if formatted_parts else "Not set."

    def format_init_memory(self) -> str:
        """Format init memory for the prompt."""
        init_memory = self.memory_manager.get_init_memory()
        if not init_memory:
            return "Not set."

        formatted_parts = []
        for key, value_obj in init_memory.items():
            if isinstance(value_obj, dict) and 'value' in value_obj:
                # New format: { "key": { "value": actual_value } }
                formatted_parts.append(f"{key}: {value_obj['value']}")
            else:
                # Old format or other format: { "key": value }
                formatted_parts.append(f"{key}: {value_obj}")

        return "\n".join(formatted_parts)

    def format_temp_memory(self) -> str:
        """Format temporary memory for the prompt."""
        temp_memory = self.memory_manager.get_temp_memory()
        if not temp_memory:
            return "Empty."

        return "\n".join([
            f"[{item.get('role', 'system')} | ID: {item.get('id', 'N/A')}] {item.get('content', '')}"
            for item in temp_memory
        ])

    def format_user_messages(self, messages: List[Dict[str, str]]) -> str:
        """Format user messages for the prompt."""
        if not messages:
            return "No recent messages."

        return "\n".join([f"{msg['user']}: {msg['content']}" for msg in messages])

    def format_tool_descriptions(self) -> str:
        """Format tool descriptions for the prompt."""
        schemas = self.tool_manager.get_tool_schemas()
        if not schemas:
            return "No tools available."

        lines = ["Available tools:"]
        for i, schema in enumerate(schemas):
            params_str_parts = []
            for param in schema.get("parameters", []):
                p_name = param.get("name")
                p_type = param.get("type")
                p_req = "required" if param.get("required") else "optional"
                params_str_parts.append(f"{p_name}: {p_type} ({p_req})")
            params_str = ", ".join(params_str_parts)
            lines.append(
                f"{i + 1}. {schema.get('name')}({params_str}) - {schema.get('description')}"
            )
        return "\n".join(lines)

    def build_system_prompt(self) -> str:
        """Build the system prompt for the LLM."""
        # Load the prompt template
        with open(self.config.PROMPT_PATH, 'r', encoding='utf-8') as f:
            prompt_template = f.read()

        # Format memory components using the memory manager
        formatted_core_memory = self.format_core_memory()
        formatted_init_memory = self.format_init_memory()
        formatted_temp_memory = self.format_temp_memory()
        formatted_tool_descriptions = self.format_tool_descriptions()

        # Build the system prompt (without current context)
        system_prompt = prompt_template.format(
            tool_descriptions=formatted_tool_descriptions,
            init_memory=formatted_init_memory,
            core_memory=formatted_core_memory,
            temp_memory=formatted_temp_memory,
        )

        return system_prompt

    def build_context(self, user_messages: List[Dict[str, str]]) -> str:
        """Build the current context for the LLM (just the user messages)."""
        formatted_user_messages = self.format_user_messages(user_messages)
        return formatted_user_messages
