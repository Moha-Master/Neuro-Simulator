# neuro_simulator/core/data_manager.py
"""Manages the reset and initialization of data directories."""

import logging
import shutil
from pathlib import Path

from .path_manager import path_manager

logger = logging.getLogger(__name__)


def reset_data_directories_to_defaults():
    """
    Deletes the neuro_data and chatbot_data directories and recreates them
    with the default files, mimicking a first-run setup.
    """
    if not path_manager:
        raise RuntimeError("Path manager is not initialized.")

    logger.info("Starting reset of data directories to default state...")

    # --- 1. Define source and destination directories ---
    neuro_data_dir = path_manager.neuro_data_dir
    chatbot_data_dir = path_manager.chatbot_data_dir
    package_source_path = Path(__file__).parent.parent  # up to neuro_simulator/

    # --- 2. Delete existing directories ---
    if neuro_data_dir.exists():
        shutil.rmtree(neuro_data_dir)
        logger.info(f"Removed existing directory: {neuro_data_dir}")
    if chatbot_data_dir.exists():
        shutil.rmtree(chatbot_data_dir)
        logger.info(f"Removed existing directory: {chatbot_data_dir}")

    # --- 3. Re-create the directory structure ---
    path_manager.initialize_directories()
    logger.info("Re-initialized all data directories.")

    # --- 4. Copy default files ---
    def copy_default_file(src: Path, dest: Path):
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dest)
            logger.info(f"Copied default file to {dest}")
        except Exception as e:
            logger.error(f"Failed to copy {src} to {dest}: {e}")

    # --- Copy Neuro Agent Files ---
    neuro_source_path = package_source_path / "agents" / "neuro"
    copy_default_file(
        neuro_source_path / "prompts" / "neuro_prompt.txt",
        path_manager.neuro_prompt_path,
    )
    copy_default_file(
        neuro_source_path / "memory" / "core_memory.json",
        path_manager.core_memory_path,
    )
    copy_default_file(
        neuro_source_path / "memory" / "init_memory.json",
        path_manager.init_memory_path,
    )
    copy_default_file(
        neuro_source_path / "memory" / "temp_memory.json",
        path_manager.temp_memory_path,
    )

    # --- Copy Chatbot Agent Files ---
    chatbot_source_path = package_source_path / "agents" / "chatbot"
    copy_default_file(
        chatbot_source_path / "prompts" / "chatbot_prompt.txt",
        path_manager.chatbot_prompt_path,
    )
    copy_default_file(
        chatbot_source_path / "nickname_gen" / "data" / "adjectives.txt",
        path_manager.chatbot_nickname_data_dir / "adjectives.txt",
    )
    copy_default_file(
        chatbot_source_path / "nickname_gen" / "data" / "nouns.txt",
        path_manager.chatbot_nickname_data_dir / "nouns.txt",
    )
    copy_default_file(
        chatbot_source_path / "nickname_gen" / "data" / "special_users.txt",
        path_manager.chatbot_nickname_data_dir / "special_users.txt",
    )

    # --- Copy Shared Assets ---
    copy_default_file(
        package_source_path / "assets" / "neuro_start.mp4",
        path_manager.assets_dir / "neuro_start.mp4",
    )

    logger.info("Finished resetting data directories.")
