# neuro_simulator/chatbot/core.py
"""
Core module for the Neuro Simulator's Chatbot agent.
Implements a single-LLM "Actor" architecture.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

from ...core.agent_interface import BaseAgent
from ...core.config import config_manager
from ...core.llm_manager import llm_manager
from ...core.path_manager import path_manager
from ..tools.manager import ToolManager
from ..output_manager import OutputManager
from .nickname_gen.generator import NicknameGenerator

logger = logging.getLogger(__name__)


class Chatbot(BaseAgent):
    """
    Chatbot Agent class implementing the Actor model and BaseAgent interface.
    """

    def __init__(self, output_callback=None):
        if not path_manager:
            raise RuntimeError(
                "PathManager must be initialized before the Chatbot agent."
            )
        if not config_manager.settings:
            raise RuntimeError("ConfigManager must be initialized before the Chatbot agent.")

        settings = config_manager.settings
        self.chatbot_llm = llm_manager.get_client(
            settings.chatbot.chatbot_llm_provider_id
        )

        # Create output manager
        self.output_manager = OutputManager(
            agent_type="chatbot",
            output_callback=output_callback
        )

        self._tool_manager = ToolManager(
            memory_manager=None,  # No memory manager for simplified chatbot
            output_manager=self.output_manager,
            builtin_tools_path=Path(__file__).parent / "tools",
            user_tools_path=path_manager.chatbot_tools_dir,
            tool_allocations_paths={
                "chatbot": path_manager.chatbot_tools_path,
            },
            default_allocations={
                "chatbot": [
                    "post_chat_message",
                ],
            }
        )
        self.nickname_generator = NicknameGenerator()

        self._initialized = False

    @property
    def tool_manager(self) -> ToolManager:
        return self._tool_manager

    async def initialize(self):
        """Initializes components that are safe to run on startup."""
        if not self._initialized:
            logger.info("Initializing Chatbot agent (startup-safe components)...")
            await self.nickname_generator.initialize()
            self.tool_manager.load_tools()
            self._initialized = True
            logger.info("Chatbot agent startup components initialized successfully.")

    async def initialize_runtime_components(self):
        """Initializes components that require a live configuration, like the LLM."""
        logger.info("Initializing Chatbot agent (runtime components)...")
        logger.info("Chatbot agent runtime components initialized successfully.")

    async def reset_memory(self):
        """Reset chatbot history logs."""
        assert path_manager is not None
        # Clear history files by overwriting them
        open(path_manager.chatbot_history_path, "w").close()
        logger.info("Chatbot history logs have been reset.")

    async def get_message_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Reads the last N lines from the Chatbot agent's history log."""
        assert path_manager is not None
        return await self._read_history(path_manager.chatbot_history_path, limit)

    async def process_and_respond(
        self,
        messages: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """Main entry point for BaseAgent, not used by Chatbot's current design."""
        logger.warning("process_and_respond is not the primary entry point for Chatbot.")
        return {"status": "not_implemented"}

    async def _append_to_history(self, file_path: Path, data: Dict[str, Any]):
        """Appends a new entry to a JSON Lines history file."""
        data["timestamp"] = datetime.now().isoformat()
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    async def _read_history(self, file_path: Path, limit: int) -> List[Dict[str, Any]]:
        """Reads the last N lines from a JSON Lines history file."""
        if not file_path.exists():
            return []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            return [json.loads(line) for line in lines[-limit:]]
        except (json.JSONDecodeError, IndexError):
            return []

    def _format_tool_schemas_for_prompt(self, agent_name: str) -> str:
        """Formats tool schemas for a specific agent (actor)."""
        schemas = self.tool_manager.get_tool_schemas_for_agent(agent_name)
        if not schemas:
            return "No tools available."
        lines = ["Available tools:"]
        for i, schema in enumerate(schemas):
            params = ", ".join(
                [
                    f"{p.get('name')}: {p.get('type')}"
                    for p in schema.get("parameters", [])
                ]
            )
            lines.append(
                f"{i + 1}. {schema.get('name')}({params}) - {schema.get('description')}"
            )
        return "\n".join(lines)

    async def build_chatbot_prompt(
        self,
        neuro_speech: str,
        recent_history: List[Dict[str, str]],
        num_messages: int,
    ) -> str:
        """Builds the prompt for the Chatbot (Actor) LLM."""
        assert path_manager is not None
        with open(path_manager.chatbot_prompt_path, "r", encoding="utf-8") as f:
            prompt_template = f.read()

        tool_descriptions = self._format_tool_schemas_for_prompt("chatbot")
        recent_history_text = "\n".join(
            [f"{msg.get('role')}: {msg.get('content')}" for msg in recent_history]
        )

        return prompt_template.format(
            tool_descriptions=tool_descriptions,
            recent_history=recent_history_text,
            neuro_speech=neuro_speech,
            chats_per_batch=num_messages,
        )

    async def build_neuro_prompt(self, messages: List[Dict[str, str]]) -> str:
        """Implements the BaseAgent requirement, but delegates to build_chatbot_prompt."""
        # This is a slight mismatch in concepts, as chatbot has a different trigger.
        # We'll use the last message as the 'neuro_speech' context.
        neuro_speech = messages[-1].get("text", "") if messages else ""
        recent_history = await self.get_message_history(limit=10)
        
        from ...core.config import config_manager
        assert config_manager.settings is not None
        chats_per_batch = config_manager.settings.chatbot.chats_per_batch
        
        return await self.build_chatbot_prompt(neuro_speech, recent_history, chats_per_batch)


    def _parse_tool_calls(self, response_text: str) -> List[Dict[str, Any]]:
        """Extracts and parses JSON objects from the LLM's response text."""
        try:
            # First, try to find a JSON array
            start_index = response_text.find("[")
            end_index = response_text.rfind("]")

            if start_index != -1 and end_index != -1 and end_index > start_index:
                # Found an array
                json_str = response_text[start_index : end_index + 1]
                return json.loads(json_str)
            else:
                # Try to find a single JSON object instead
                start_index = response_text.find("{")
                end_index = response_text.rfind("}")

                if start_index != -1 and end_index != -1 and end_index > start_index:
                    # Found a single object
                    json_str = response_text[start_index : end_index + 1]
                    single_obj = json.loads(json_str)
                    # Convert single object to list
                    if isinstance(single_obj, dict):
                        return [single_obj]
                    elif isinstance(single_obj, list):
                        return single_obj
                    else:
                        logger.warning(
                            f"Unexpected JSON type in response: {type(single_obj)}"
                        )
                        return []
                else:
                    logger.warning(
                        f"Could not find a valid JSON object or array in response: {response_text}"
                    )
                    return []
        except json.JSONDecodeError as e:
            logger.warning(
                f"Failed to decode JSON from LLM response: {e}\nRaw text: {response_text}"
            )
            return []
        except Exception as e:
            logger.error(
                f"Failed to parse tool calls from LLM response: {e}\nRaw text: {response_text}"
            )
            return []

    async def _execute_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
        agent_name: str,
    ) -> List[Dict[str, str]]:
        assert path_manager is not None
        generated_messages = []
        for tool_call in tool_calls:
            # Handle nested lists or ensure we have a valid dict
            if isinstance(tool_call, list):
                # If tool_call is a list, process each item in the list
                for sub_call in tool_call:
                    if isinstance(sub_call, dict):
                        await self._execute_single_tool_call(sub_call, agent_name, generated_messages)
            elif isinstance(tool_call, dict):
                # If it's a dictionary, process it directly
                await self._execute_single_tool_call(tool_call, agent_name, generated_messages)
            else:
                logger.warning(f"Unexpected tool_call type: {type(tool_call)}, value: {tool_call}")
        logger.debug(f"Returning generated messages: {generated_messages}")
        return generated_messages

    async def _execute_single_tool_call(self, tool_call: Dict[str, Any], agent_name: str, generated_messages: List[Dict[str, str]]):
        """Execute a single tool call and add to generated messages if appropriate."""
        tool_name = tool_call.get("name")
        if not tool_name:
            logger.warning(f"Tool call missing name: {tool_call}")
            return
        params = tool_call.get("params", {})
        result = await self.tool_manager.execute_tool(tool_name, **params)

        if (
            agent_name == "chatbot"
            and tool_name == "post_chat_message"
            and result.get("status") == "success"
        ):
            text_to_post = result.get("text_to_post", "")
            if text_to_post:
                nickname = self.nickname_generator.generate_nickname()
                message = {"username": nickname, "text": text_to_post}
                generated_messages.append(message)
                await self._append_to_history(
                    path_manager.chatbot_history_path,
                    {"role": "assistant", "content": f"{nickname}: {text_to_post}"},
                )

                # Send chat message through output manager
                if hasattr(self, 'output_manager') and self.output_manager:
                    await self.output_manager.send_custom_output(
                        output_type="chat_message",
                        payload={
                            "username": nickname,
                            "text": text_to_post
                        }
                    )


    async def _generate_contextual_chats(
        self,
        neuro_speech: str,
        recent_history: List[Dict[str, str]],
        num_contextual: int,
    ) -> List[Dict[str, str]]:
        """Generates chat messages that are contextually relevant to Neuro's speech."""
        prompt = await self.build_chatbot_prompt(
            neuro_speech, recent_history, num_contextual
        )
        response_text = await self.chatbot_llm.generate(prompt)
        if not response_text:
            return []

        tool_calls = self._parse_tool_calls(response_text)
        if not tool_calls:
            return []

        return await self._execute_tool_calls(tool_calls, "chatbot")


    async def generate_chat_messages(
        self,
        neuro_speech: Optional[str],
        recent_history: List[Dict[str, str]],
    ) -> List[Dict[str, str]]:
        """
        The main actor loop to generate chat messages based on context.
        """
        if not self.chatbot_llm:
            logger.warning("Chatbot LLM is not configured. Skipping message generation.")
            return []

        assert path_manager is not None
        for entry in recent_history:
            await self._append_to_history(path_manager.chatbot_history_path, entry)

        settings = config_manager.settings.chatbot
        chats_per_batch = settings.chats_per_batch

        # Determine the contextual speech to use
        contextual_speech = neuro_speech
        if not contextual_speech:
            contextual_speech = settings.initial_prompt

        # Generate contextual chats based on Neuro's speech
        contextual_messages = await self._generate_contextual_chats(
            contextual_speech, recent_history, chats_per_batch
        )

        return contextual_messages


    # --- Implementation of BaseAgent interface methods ---

    # Memory Block Management - Not implemented for simplified chatbot
    async def get_memory_blocks(self) -> List[Dict[str, Any]]:
        return []

    async def get_memory_block(self, block_id: str) -> Optional[Dict[str, Any]]:
        return None

    async def create_memory_block(
        self,
        title: str,
        description: str,
        content: List[str],
    ) -> Dict[str, str]:
        return {"block_id": "not_implemented"}

    async def update_memory_block(
        self,
        block_id: str,
        title: Optional[str],
        description: Optional[str],
        content: Optional[List[str]],
    ):
        pass

    async def delete_memory_block(self, block_id: str):
        pass

    # Init Memory Management - Not implemented for simplified chatbot
    async def get_init_memory(self) -> Dict[str, Any]:
        return {}

    async def update_init_memory(self, memory: Dict[str, Any]):
        pass

    async def update_init_memory_item(self, key: str, value: Any):
        pass

    async def delete_init_memory_key(self, key: str):
        pass

    # Temp Memory Management - Not implemented for simplified chatbot
    async def get_temp_memory(self) -> List[Dict[str, Any]]:
        return []

    async def add_temp_memory(self, content: str, role: str):
        pass

    async def delete_temp_memory_item(self, item_id: str):
        pass

    async def clear_temp_memory(self):
        pass

    # Tool Management
    async def get_available_tools(self) -> List[Dict[str, Any]]:
        return self.tool_manager.get_tool_schemas_for_agent("chatbot")

    async def execute_tool(self, tool_name: str, params: Dict[str, Any]) -> Any:
        return await self.tool_manager.execute_tool(tool_name, **params)