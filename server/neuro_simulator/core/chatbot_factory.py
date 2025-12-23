# neuro_simulator/core/chatbot_factory.py
import logging
from typing import Optional

from .agent_interface import BaseAgent
from .config import config_manager, AppSettings
from ..agents.chatbot.core import Chatbot

logger = logging.getLogger(__name__)

# A cache for the chatbot instance to avoid re-initialization
_chatbot_instance: Optional[BaseAgent] = None


def _reset_chatbot_on_config_update(new_settings: AppSettings):
    """Resets the cached chatbot instance when configuration is updated."""
    global _chatbot_instance
    logger.info("Configuration has been updated. Resetting cached Chatbot instance.")
    _chatbot_instance = None


# Register the callback to the config manager
config_manager.register_update_callback(_reset_chatbot_on_config_update)


async def create_chatbot(force_recreate: bool = False, output_callback=None) -> Optional[BaseAgent]:
    """
    Factory function to create and initialize the Chatbot agent instance.
    Returns a cached instance unless the configuration has changed or output_callback is specified.
    Returns None if the chatbot is not configured.

    Args:
        force_recreate: If True, forces the recreation of the agent instance.
        output_callback: Optional callback for agent outputs. When specified, always creates a new instance.
    """
    global _chatbot_instance

    # If output_callback is provided, always create a new instance to ensure it has the callback
    if output_callback is not None:
        logger.debug("Creating new Chatbot agent instance with output callback...")
        try:
            # Directly instantiate and initialize the Chatbot agent with callback
            agent = Chatbot(output_callback=output_callback)
            await agent.initialize()
            logger.debug("New Chatbot agent instance with callback created successfully.")
            return agent
        except Exception as e:
            logger.critical(f"Failed to create and initialize Chatbot agent with callback: {e}", exc_info=True)
            # We don't re-raise here because a missing chatbot might be a valid state
            return None

    if force_recreate:
        logger.info("Forcing recreation of Chatbot instance.")
        _chatbot_instance = None

    if _chatbot_instance is not None:
        return _chatbot_instance

    logger.debug("Creating new Chatbot agent instance...")

    try:
        # Directly instantiate and initialize the Chatbot agent without callback (for legacy use)
        agent = Chatbot()
        await agent.initialize()
        _chatbot_instance = agent
        logger.debug("New Chatbot agent instance created and cached.")
        return _chatbot_instance
    except Exception as e:
        logger.critical(f"Failed to create and initialize Chatbot agent: {e}", exc_info=True)
        # In case of failure, ensure we don't return a partially initialized object
        _chatbot_instance = None
        # We don't re-raise here because a missing chatbot might be a valid state
        return None
