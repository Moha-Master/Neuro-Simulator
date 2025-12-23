"""Output manager for the Neuro Sama module."""

import json
from typing import Dict, Any


class OutputManager:
    """Manages the formatting and sending of output messages."""
    
    @staticmethod
    def create_output_pack(output_type: str, payload: Dict[str, Any], input_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Creates a standardized output package.
        
        Args:
            output_type: Type of output (e.g., 'response', 'speak', 'completion', 'error', 'info')
            payload: The actual data for this output
            input_data: Original input that triggered this output (optional)
            
        Returns:
            A standardized output package
        """
        output_pack = {
            "type": output_type,
            "payload": payload
        }
        
        if input_data:
            output_pack["input"] = input_data
            
        return output_pack
    
    @staticmethod
    def create_speak_output(text: str, audio_base64: str = "", duration: float = 0.0, input_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Creates an output package for speak content with audio."""
        payload = {
            "text": text,
            "audio_base64": audio_base64,
            "duration": duration
        }
        return OutputManager.create_output_pack("speak", payload, input_data)
    
    @staticmethod
    def create_completion_output(input_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Creates an output package for completion marker."""
        payload = {
            "message": "Response completed"
        }
        return OutputManager.create_output_pack("completion", payload, input_data)
    
    @staticmethod
    def create_error_output(message: str, input_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Creates an output package for error messages."""
        payload = {
            "message": message
        }
        return OutputManager.create_output_pack("error", payload, input_data)
    
    @staticmethod
    def create_info_output(message: str, raw_response: str = None, input_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Creates an output package for info messages."""
        payload = {
            "message": message
        }
        if raw_response:
            payload["raw_response"] = raw_response
        return OutputManager.create_output_pack("info", payload, input_data)