"""Streaming JSON parser for processing JSON responses incrementally."""

import json
from typing import List, Dict, Any, Optional


class StreamingJSONParser:
    """A streaming JSON parser that can extract complete JSON objects from a stream."""

    def __init__(self):
        self.buffer = ""

    def feed(self, text: str) -> List[Dict[str, Any]]:
        """Feed text to the parser and return any complete JSON objects found."""
        self.buffer += text
        objects = []

        # Look for JSON objects in the buffer
        while True:
            # Find the start of a potential JSON object
            start_idx = self._find_next_json_start(self.buffer)
            if start_idx == -1:
                break  # No more JSON objects to process

            # Find the corresponding end of the JSON object
            end_idx = self._find_matching_bracket_end(self.buffer, start_idx)
            if end_idx == -1:
                # The JSON object is not complete yet, wait for more data
                break

            # Extract the potential JSON string
            json_str = self.buffer[start_idx:end_idx+1]
            try:
                # Try to parse it
                obj = json.loads(json_str)
                objects.append(obj)

                # Remove the processed part from the buffer
                self.buffer = self.buffer[end_idx+1:]
            except json.JSONDecodeError:
                # If parsing fails, it might be due to malformed JSON
                # Skip this potential object and continue
                # Move past the opening brace to look for the next potential object
                self.buffer = self.buffer[start_idx+1:]

        return objects

    def _find_next_json_start(self, text: str) -> int:
        """Find the index of the next JSON object start."""
        in_string = False
        escape_next = False

        for i, char in enumerate(text):
            if escape_next:
                escape_next = False
                continue

            if char == '\\':
                escape_next = True
                continue

            if char == '"' and not escape_next:
                in_string = not in_string
                continue

            if not in_string and char == '{':
                return i

        return -1

    def _find_matching_bracket_end(self, text: str, start: int) -> int:
        """Find the matching closing bracket for a JSON object."""
        nested_level = 0
        in_string = False
        escape_next = False

        for i in range(start, len(text)):
            char = text[i]

            if escape_next:
                escape_next = False
                continue

            if char == '\\':
                escape_next = True
                continue

            if not in_string:
                if char == '{':
                    nested_level += 1
                elif char == '}':
                    nested_level -= 1
                    if nested_level == 0:
                        return i
                elif char == '"':
                    in_string = True
            else:
                if char == '"':
                    in_string = False

        return -1  # Not found

    def get_remaining_buffer(self) -> str:
        """Get any remaining text in the buffer."""
        return self.buffer

    def reset(self):
        """Reset the parser."""
        self.buffer = ""