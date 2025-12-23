# neuro_simulator/core/path_manager.py
"""Manages all file and directory paths for the application's working directory."""

import os
from pathlib import Path
from typing import Optional


class PathManager:
    """A centralized manager for all dynamic paths within the working directory."""

    def __init__(self, working_dir: str):
        """Initializes the PathManager and defines the directory structure."""
        self.working_dir = Path(working_dir).resolve()

        # --- Main Agent Paths ---
        self.neuro_data_dir = self.working_dir / "neuro_data"
        self.assets_dir = self.working_dir / "assets"
        self.neuro_agent_dir = self.neuro_data_dir / "neuro"
        self.shared_memories_dir = self.neuro_data_dir / "memories"
        self.user_tools_dir = self.neuro_data_dir / "tools"

        self.neuro_tools_path = self.neuro_agent_dir / "tools.json"
        self.neuro_history_path = self.neuro_agent_dir / "history.jsonl"
        self.neuro_prompt_path = self.neuro_agent_dir / "neuro_prompt.txt"

        self.init_memory_path = self.shared_memories_dir / "init_memory.json"
        self.core_memory_path = self.shared_memories_dir / "core_memory.json"
        self.temp_memory_path = self.shared_memories_dir / "temp_memory.json"

        # --- Chatbot Paths ---
        self.chatbot_data_dir = self.working_dir / "chatbot_data"
        self.chatbot_dir = self.chatbot_data_dir / "chatbot"
        self.chatbot_memories_dir = self.chatbot_data_dir / "memories"
        self.chatbot_tools_dir = self.chatbot_data_dir / "tools"
        self.chatbot_nickname_data_dir = self.chatbot_data_dir / "nickname_gen" / "data"

        self.chatbot_prompt_path = self.chatbot_dir / "chatbot_prompt.txt"
        self.chatbot_tools_path = self.chatbot_dir / "tools.json"
        self.chatbot_history_path = self.chatbot_dir / "history.jsonl"


    def initialize_directories(self):
        """Creates all necessary directories if they don't exist."""
        dirs_to_create = [
            self.neuro_data_dir,
            self.assets_dir,
            self.neuro_agent_dir,
            self.shared_memories_dir,
            self.user_tools_dir,
            self.chatbot_data_dir,
            self.chatbot_dir,
            self.chatbot_memories_dir,
            self.chatbot_tools_dir,
            self.chatbot_nickname_data_dir,
        ]
        for dir_path in dirs_to_create:
            os.makedirs(dir_path, exist_ok=True)


# A global instance that can be imported and used by other modules.
# It will be initialized on application startup.
path_manager: Optional[PathManager] = None


def initialize_path_manager(working_dir: str):
    """Initializes the global path_manager instance."""
    global path_manager
    if path_manager is None:
        path_manager = PathManager(working_dir)
        path_manager.initialize_directories()
