# neuro_simulator/chatbot/nickname_gen/generator.py
"""
Nickname generator for the chatbot agent.
Uses only pre-defined word pools from data files.
"""

import logging
import random
from typing import Any, List, Dict, Callable

from ....core.path_manager import path_manager
from ....utils import console

logger = logging.getLogger(__name__)


class NicknameGenerator:
    """Generates diverse nicknames using pre-defined word pools."""

    def __init__(self):
        if not path_manager:
            raise RuntimeError(
                "PathManager must be initialized before NicknameGenerator."
            )

        self.base_adjectives: List[str] = []
        self.base_nouns: List[str] = []
        self.special_users: List[str] = []

    def _load_word_pool(self, filename: str) -> List[str]:
        """Loads a word pool from the nickname_gen/data directory."""
        assert path_manager is not None
        file_path = path_manager.chatbot_nickname_data_dir / filename
        if not file_path.exists():
            logger.warning(
                f"Nickname pool file not found: {file_path}. The pool will be empty."
            )
            return []
        with open(file_path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]

    async def initialize(self):
        """Loads base pools."""
        logger.info("Initializing NicknameGenerator...")
        self.base_adjectives = self._load_word_pool("adjectives.txt")
        self.base_nouns = self._load_word_pool("nouns.txt")
        self.special_users = self._load_word_pool("special_users.txt")

        if not self.base_adjectives or not self.base_nouns:
            logger.warning(
                "Base adjective or noun pools are empty. Nickname generation quality will be affected."
            )

        logger.info("NicknameGenerator initialized.")

    def _get_pools(self) -> tuple[List[str], List[str]]:
        """Returns the base pools."""
        return self.base_adjectives, self.base_nouns

    def _generate_from_word_pools(self) -> str:
        adjectives, nouns = self._get_pools()
        if not adjectives or not nouns:
            return self._generate_random_numeric()  # Fallback

        noun = random.choice(nouns)

        # 50% chance to add an adjective
        if random.random() < 0.5:
            adjective = random.choice(adjectives)
            # Formatting variations
            format_choice = random.random()
            if format_choice < 0.4:
                return f"{adjective.capitalize()}{noun.capitalize()}"
            elif format_choice < 0.7:
                return f"{adjective.lower()}_{noun.lower()}"
            else:
                return f"{adjective.lower()}{noun.lower()}"
        else:
            # Add a number suffix 30% of the time
            if random.random() < 0.3:
                return f"{noun.capitalize()}{random.randint(1, 999)}"
            return noun.capitalize()

    def _generate_from_special_pool(self) -> str:
        if not self.special_users:
            return self._generate_from_word_pools()  # Fallback
        return random.choice(self.special_users)

    def _generate_random_numeric(self) -> str:
        return f"user{random.randint(10000, 99999)}"

    def generate_nickname(self) -> str:
        """Generates a single nickname based on weighted strategies."""
        from typing import Dict, Callable  # Import here to avoid circular import issues
        strategies: Dict[Callable[[], str], int] = {
            self._generate_from_word_pools: 70,
            self._generate_from_special_pool: 15,
            self._generate_random_numeric: 15,
        }

        # Filter out strategies that can't be run (e.g., empty special pool)
        if not self.special_users:
            strategies.pop(self._generate_from_special_pool, None)
            # Redistribute weight
            if strategies:
                total_weight = sum(strategies.values())
                strategies = {k: int(v / total_weight * 100) for k, v in strategies.items()}

        if not any(self._get_pools()):
            strategies = {self._generate_random_numeric: 100}

        chosen_strategy = random.choices(
            population=list(strategies.keys()), weights=list(strategies.values()), k=1
        )[0]

        return chosen_strategy()
