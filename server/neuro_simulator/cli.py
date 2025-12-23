#!/usr/bin/env python3
"""Command-line interface for the Neuro-Simulator Server."""

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path


def main():
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(description="Neuro-Simulator Server")
    parser.add_argument("-D", "--dir", help="Working directory for config and data")
    parser.add_argument("-H", "--host", help="Host to bind the server to")
    parser.add_argument("-P", "--port", type=int, help="Port to bind the server to")

    args = parser.parse_args()

    # --- 1. Setup Working Directory ---
    if args.dir:
        work_dir = Path(args.dir).resolve()
        if not work_dir.exists():
            logging.error(
                f"Working directory '{work_dir}' does not exist. Please create it first."
            )
            sys.exit(1)
    else:
        work_dir = Path.home() / ".config" / "neuro-simulator"
        work_dir.mkdir(parents=True, exist_ok=True)

    os.chdir(work_dir)
    os.environ['NEURO_SIM_WORK_DIR'] = str(work_dir)
    logging.info(f"Using working directory: {work_dir}")

    # --- 2. Initialize Path Manager ---
    from neuro_simulator.core import path_manager

    path_manager.initialize_path_manager(os.getcwd())

    # --- 3. First-Run Environment Initialization ---
    try:
        # This block ensures that a new user has all the necessary default files.
        main_config_path = path_manager.path_manager.working_dir / "config.yaml"

        # Generate a blank config.yaml if it doesn't exist. 
        # The ConfigManager will populate it with defaults on first load.
        if not main_config_path.exists():
            os.environ['NEURO_SIM_FIRST_RUN'] = 'true'
            logging.info(f"Config file not found. Generating a blank config at {main_config_path}")
            with open(main_config_path, "w", encoding="utf-8") as f:
                f.write("{}\n") # Write an empty YAML object
            logging.info("Successfully generated blank config file.")

        # --- Copy other asset and prompt files ---
        package_source_path = Path(__file__).parent

        def copy_if_not_exists(src: Path, dest: Path):
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, dest)
                logging.info(f"Copied default file to {dest}")

        # --- Copy Neuro Agent Files ---
        neuro_source_path = package_source_path / "agents" / "neuro"
        copy_if_not_exists(
            neuro_source_path / "prompts" / "neuro_prompt.txt",
            path_manager.path_manager.neuro_prompt_path,
        )
        copy_if_not_exists(
            neuro_source_path / "memory" / "core_memory.json",
            path_manager.path_manager.core_memory_path,
        )
        copy_if_not_exists(
            neuro_source_path / "memory" / "init_memory.json",
            path_manager.path_manager.init_memory_path,
        )
        copy_if_not_exists(
            neuro_source_path / "memory" / "temp_memory.json",
            path_manager.path_manager.temp_memory_path,
        )

        # --- Copy Chatbot Agent Files ---
        chatbot_source_path = package_source_path / "agents" / "chatbot"
        copy_if_not_exists(
            chatbot_source_path / "prompts" / "chatbot_prompt.txt",
            path_manager.path_manager.chatbot_prompt_path,
        )
        copy_if_not_exists(
            chatbot_source_path / "nickname_gen" / "data" / "adjectives.txt",
            path_manager.path_manager.chatbot_nickname_data_dir / "adjectives.txt",
        )
        copy_if_not_exists(
            chatbot_source_path / "nickname_gen" / "data" / "nouns.txt",
            path_manager.path_manager.chatbot_nickname_data_dir / "nouns.txt",
        )
        copy_if_not_exists(
            chatbot_source_path / "nickname_gen" / "data" / "special_users.txt",
            path_manager.path_manager.chatbot_nickname_data_dir / "special_users.txt",
        )

        # --- Copy Shared Assets ---
        copy_if_not_exists(
            package_source_path / "assets" / "neuro_start.mp4",
            path_manager.path_manager.assets_dir / "neuro_start.mp4",
        )
    except Exception as e:
        logging.warning(f"Could not copy all default files: {e}")
    from neuro_simulator.core.config import config_manager
    from pydantic import ValidationError
    import uvicorn

    main_config_path = path_manager.path_manager.working_dir / "config.yaml"
    try:
        config_manager.load(str(main_config_path))
    except ValidationError as e:
        logging.error(f"FATAL: Configuration error in '{main_config_path.name}':")
        logging.error(e)
        sys.exit(1)
    except Exception as e:
        logging.error(
            f"FATAL: An unexpected error occurred while loading the configuration: {e}"
        )
        sys.exit(1)

    # --- 5. Determine Server Host and Port ---
    # Command-line arguments override config file settings
    server_host = args.host or config_manager.settings.server.host
    server_port = args.port or config_manager.settings.server.port
    os.environ['NEURO_SIM_HOST'] = server_host
    os.environ['NEURO_SIM_PORT'] = str(server_port)

    # --- 6. Run the Server ---
    logging.info(f"Starting Neuro-Simulator server on {server_host}:{server_port}...")
    try:
        uvicorn.run(
            "neuro_simulator.core.application:app",
            host=server_host,
            port=server_port,
            reload=False,
        )
    except ImportError as e:
        logging.error(
            f"Could not import the application. Make sure the package is installed correctly. Details: {e}",
            exc_info=True,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
