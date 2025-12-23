"""Base tool class for the Neuro Sama module."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class BaseTool(ABC):
    """Base class for all tools."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """The name of the tool."""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """A description of what the tool does."""
        pass
    
    @property
    @abstractmethod
    def parameters(self) -> List[Dict[str, Any]]:
        """A list of parameters for the tool."""
        pass
    
    @abstractmethod
    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """Execute the tool with the given parameters."""
        pass