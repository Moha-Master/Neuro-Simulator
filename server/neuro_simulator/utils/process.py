# neuro_simulator/utils/process.py
import asyncio
import logging

from .websocket import connection_manager

logger = logging.getLogger(__name__)


class ProcessManager:
    """Manages the lifecycle of core background tasks for the stream."""

    def __init__(self):
        self._tasks: list[asyncio.Task] = []
        self._is_running = False
        logger.info("ProcessManager initialized.")

    @property
    def is_running(self) -> bool:
        """Returns True if the core stream processes are running."""
        # Import here to avoid circular import
        from ..core.application import stream_manager
        return stream_manager.is_running

    async def start_live_processes(self):
        """
        Starts all background tasks related to the live stream.
        Now delegates to stream_manager for the actual implementation.
        """
        if self.is_running:
            logger.warning("Processes are already running.")
            return

        logger.info("Starting core stream processes via stream_manager...")

        # Import here to avoid circular import
        from ..core.application import stream_manager
        from ..utils.queue import clear_all_queues

        # Clear queues before starting
        clear_all_queues()

        # Start the stream using the stream manager
        await stream_manager.start_stream()

        # Broadcast stream status update
        status = stream_manager.get_status()
        await connection_manager.broadcast_to_admins(
            {"type": "stream_status", "payload": status}
        )
        logger.info(f"Core processes started: stream_manager.is_running={stream_manager.is_running}.")

    async def stop_live_processes(self):
        """Stops and cleans up all running background tasks."""
        logger.info("Broadcasting offline message before stopping tasks...")
        await connection_manager.broadcast({"type": "offline"})
        await asyncio.sleep(0.1)  # Give a brief moment for the message to be sent

        logger.info("Stopping core stream processes via stream_manager...")

        # Import here to avoid circular import
        from ..core.application import stream_manager

        # Stop the stream using the stream manager
        await stream_manager.stop_stream()

        logger.info("All core tasks have been stopped.")


# Global singleton instance
process_manager = ProcessManager()
