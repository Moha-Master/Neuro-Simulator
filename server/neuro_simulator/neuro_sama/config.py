"""Configuration for the Neuro Sama module."""

import os
from typing import Dict, Any


class Config:
    """Configuration class for Neuro Sama module."""

    def __init__(self, global_config: Dict[str, Any], working_dir: str = None):
        """
        Initialize configuration from global config.

        Args:
            global_config: Global configuration dictionary from central framework
            working_dir: Working directory for the module
        """
        # Extract Neuro Sama specific config
        neuro_sama_config = global_config.get("neuro_sama", {})

        # Get service IDs
        llm_service_id = neuro_sama_config.get("llm_service_id", "")
        tts_service_id = neuro_sama_config.get("tts_service_id", "")

        if not llm_service_id:
            raise ValueError("Missing required configuration: neuro_sama.llm_service_id")
        if not tts_service_id:
            raise ValueError("Missing required configuration: neuro_sama.tts_service_id")

        # Find the specified LLM service
        llm_service = None
        for service in global_config.get("general", {}).get("llm_services", []):
            if service.get("id") == llm_service_id:
                llm_service = service
                break

        if not llm_service:
            raise ValueError(f"LLM service with ID '{llm_service_id}' not found in general.llm_services")

        # Find the specified TTS service
        tts_service = None
        for service in global_config.get("general", {}).get("tts_services", []):
            if service.get("id") == tts_service_id:
                tts_service = service
                break

        if not tts_service:
            raise ValueError(f"TTS service with ID '{tts_service_id}' not found in general.tts_services")

        # Validate LLM service configuration
        if "key" not in llm_service or not llm_service["key"]:
            raise ValueError(f"Missing required configuration in LLM service '{llm_service_id}': key")
        if "url" not in llm_service or not llm_service["url"]:
            raise ValueError(f"Missing required configuration in LLM service '{llm_service_id}': url")
        if "model" not in llm_service or not llm_service["model"]:
            raise ValueError(f"Missing required configuration in LLM service '{llm_service_id}': model")

        # Validate TTS service configuration
        if "key" not in tts_service or not tts_service["key"]:
            raise ValueError(f"Missing required configuration in TTS service '{tts_service_id}': key")
        if "region" not in tts_service or not tts_service["region"]:
            raise ValueError(f"Missing required configuration in TTS service '{tts_service_id}': region")
        if "timeout" not in tts_service:
            raise ValueError(f"Missing required configuration in TTS service '{tts_service_id}': timeout")

        # Set configuration values
        self.OPENAI_API_KEY = llm_service["key"]
        self.OPENAI_BASE_URL = llm_service["url"]
        self.OPENAI_MODEL = llm_service["model"]

        self.AZURE_TTS_KEY = tts_service["key"]
        self.AZURE_TTS_REGION = tts_service["region"]
        self.AZURE_TTS_TIMEOUT = float(tts_service["timeout"])

        # Server configuration
        server_config = neuro_sama_config.get("server_settings", {})
        if "host" not in server_config or not server_config["host"]:
            raise ValueError("Missing required configuration: neuro_sama.server_settings.host")
        self.HOST = server_config["host"]

        if "port" not in server_config:
            raise ValueError("Missing required configuration: neuro_sama.server_settings.port")
        self.PORT = int(server_config["port"])

        # Paths to memory and prompt files - require working directory
        if not working_dir:
            raise ValueError("Working directory is required for Neuro Sama module")

        self.PROMPT_PATH = os.path.join(working_dir, "prompts", "neuro_prompt.txt")
        self.CORE_MEMORY_PATH = os.path.join(working_dir, "memory", "core_memory.json")
        self.INIT_MEMORY_PATH = os.path.join(working_dir, "memory", "init_memory.json")
        self.TEMP_MEMORY_PATH = os.path.join(working_dir, "memory", "temp_memory.json")