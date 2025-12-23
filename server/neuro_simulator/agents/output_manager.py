"""
Output manager for agents to handle and package different types of outputs.
"""

import asyncio
import time
from typing import Any, Dict, Callable, Optional
from datetime import datetime


class OutputManager:
    """
    Manages different types of outputs from agent tools and packages them
    into standardized output packs for external consumption.
    """
    
    def __init__(self, agent_type: str, output_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        """
        Initialize the output manager.
        
        Args:
            agent_type: Type of agent ("neuro" or "chatbot")
            output_callback: Optional callback to handle output packs
        """
        self.agent_type = agent_type
        self.output_callback = output_callback
        self._output_queue = asyncio.Queue()
        
    async def send_speak_output(self, text: str, audio_base64: Optional[str] = None, duration: Optional[float] = None):
        """
        Package and send a speak output.
        
        Args:
            text: The text to speak
            audio_base64: Optional base64 encoded audio data
            duration: Optional audio duration in seconds
        """
        payload = {
            "text": text
        }
        if audio_base64:
            payload["audio_base64"] = audio_base64
        if duration:
            payload["duration"] = duration
            
        output_pack = {
            "type": "speak",
            "timestamp": time.time(),
            "payload": payload,
            "metadata": {
                "agent_type": self.agent_type,
                "source_tool": "speak"
            }
        }
        
        await self._send_output(output_pack)
    
    async def send_model_output(self, action: str):
        """
        Package and send a model action output.
        
        Args:
            action: The model action ("spin", "zoom", etc.)
        """
        output_pack = {
            "type": "model_action",
            "timestamp": time.time(),
            "payload": {
                "action": action
            },
            "metadata": {
                "agent_type": self.agent_type,
                "source_tool": action
            }
        }
        
        await self._send_output(output_pack)
    
    async def send_custom_output(self, output_type: str, payload: Dict[str, Any]):
        """
        Package and send a custom output.
        
        Args:
            output_type: Type of the output
            payload: The output payload
        """
        output_pack = {
            "type": output_type,
            "timestamp": time.time(),
            "payload": payload,
            "metadata": {
                "agent_type": self.agent_type,
                "source_tool": "custom"
            }
        }
        
        await self._send_output(output_pack)
    
    async def _send_output(self, output_pack: Dict[str, Any]):
        """
        Send the output pack via callback or queue.
        
        Args:
            output_pack: The output pack to send
        """
        if self.output_callback:
            try:
                self.output_callback(output_pack)
            except Exception as e:
                print(f"Error in output callback: {e}")
        
        # Also put in queue for synchronous access if needed
        try:
            self._output_queue.put_nowait(output_pack)
        except asyncio.QueueFull:
            # Remove oldest if queue is full
            try:
                self._output_queue.get_nowait()
                self._output_queue.put_nowait(output_pack)
            except:
                pass  # Queue operations failed, ignore
    
    def get_output_queue(self):
        """
        Get the output queue for external consumption.
        
        Returns:
            The asyncio queue containing output packs
        """
        return self._output_queue