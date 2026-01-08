"""Memory manager for the Neuro Sama module."""

import json
import os
from typing import Dict, Any, List, Optional


class MemoryManager:
    """Manages the three types of memory (init, core, temp) for the Neuro Sama module."""

    def __init__(self, config, on_memory_change_callback=None):
        self.config = config
        self.on_memory_change_callback = on_memory_change_callback

    def _notify_memory_change(self):
        """Notify that memory has changed."""
        if self.on_memory_change_callback:
            try:
                # Get current memory state
                init_memory = self.get_init_memory()
                core_memory = self.get_core_memory_blocks()
                temp_memory = self.get_temp_memory()
                
                # Call the callback with current memory state
                self.on_memory_change_callback(init_memory, core_memory, temp_memory)
            except Exception as e:
                print(f"Error in memory change callback: {e}")

    # --- Init Memory Management ---

    def get_init_memory(self) -> Dict[str, Any]:
        """Get the init memory."""
        if os.path.exists(self.config.INIT_MEMORY_PATH):
            with open(self.config.INIT_MEMORY_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def update_init_memory(self, memory: Dict[str, Any]) -> bool:
        """Update the entire init memory."""
        try:
            with open(self.config.INIT_MEMORY_PATH, 'w', encoding='utf-8') as f:
                json.dump(memory, f, ensure_ascii=False, indent=2)
            # Notify that memory has changed
            self._notify_memory_change()
            return True
        except Exception as e:
            print(f"Error updating init memory: {e}")
            return False

    def update_init_memory_item(self, key: str, value: Any) -> bool:
        """Update a single key-value pair in init memory."""
        memory = self.get_init_memory()
        memory[key] = value
        return self.update_init_memory(memory)

    def delete_init_memory_key(self, key: str) -> bool:
        """Delete a key from init memory."""
        memory = self.get_init_memory()
        if key in memory:
            del memory[key]
            return self.update_init_memory(memory)
        return False

    # --- Core Memory Management ---

    def get_core_memory_blocks(self) -> Dict[str, Any]:
        """Get all core memory blocks."""
        if os.path.exists(self.config.CORE_MEMORY_PATH):
            with open(self.config.CORE_MEMORY_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Return the blocks dictionary
                return data.get('blocks', {})
        return {}

    def get_core_memory_block(self, block_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific core memory block by ID."""
        blocks = self.get_core_memory_blocks()
        return blocks.get(block_id)

    def create_core_memory_block(self, title: str, description: str, content: List[str]) -> str:
        """Create a new core memory block."""
        blocks = self.get_core_memory_blocks()

        # Generate a unique ID (simple approach: use title as ID, make it unique)
        block_id = title.lower().replace(' ', '_').replace('-', '_')
        original_id = block_id

        counter = 1
        while block_id in blocks:
            block_id = f"{original_id}_{counter}"
            counter += 1

        # Create the new block
        new_block = {
            "id": block_id,
            "title": title,
            "description": description,
            "content": content
        }

        # Add to blocks
        blocks[block_id] = new_block

        # Save back to file
        self._save_core_memory_blocks(blocks)

        return block_id

    def update_core_memory_block(self, block_id: str, title: Optional[str] = None,
                                description: Optional[str] = None,
                                content: Optional[List[str]] = None) -> bool:
        """Update an existing core memory block."""
        blocks = self.get_core_memory_blocks()

        if block_id not in blocks:
            return False

        block = blocks[block_id]

        if title is not None:
            block['title'] = title
        if description is not None:
            block['description'] = description
        if content is not None:
            block['content'] = content

        # Update the ID field as well to match the block_id key
        block['id'] = block_id

        # Save back to file
        self._save_core_memory_blocks(blocks)
        return True

    def delete_core_memory_block(self, block_id: str) -> bool:
        """Delete a core memory block."""
        blocks = self.get_core_memory_blocks()

        if block_id in blocks:
            del blocks[block_id]
            self._save_core_memory_blocks(blocks)
            return True
        return False

    def add_to_core_memory_block(self, block_id: str, content_item: str) -> bool:
        """Add a content item to a core memory block."""
        blocks = self.get_core_memory_blocks()

        if block_id not in blocks:
            return False

        if content_item not in blocks[block_id]['content']:
            blocks[block_id]['content'].append(content_item)
            self._save_core_memory_blocks(blocks)
            return True
        return False

    def remove_from_core_memory_block(self, block_id: str, content_item: str) -> bool:
        """Remove a content item from a core memory block."""
        blocks = self.get_core_memory_blocks()

        if block_id not in blocks:
            return False

        if content_item in blocks[block_id]['content']:
            blocks[block_id]['content'].remove(content_item)
            self._save_core_memory_blocks(blocks)
            return True
        return False

    def _save_core_memory_blocks(self, blocks: Dict[str, Any]) -> bool:
        """Helper method to save core memory blocks to file."""
        try:
            # Create the structure expected by the file format
            data = {"blocks": blocks}
            with open(self.config.CORE_MEMORY_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # Notify that memory has changed
            self._notify_memory_change()
            return True
        except Exception as e:
            print(f"Error saving core memory blocks: {e}")
            return False

    # --- Temp Memory Management ---

    def get_temp_memory(self) -> List[Dict[str, Any]]:
        """Get the temp memory."""
        if os.path.exists(self.config.TEMP_MEMORY_PATH):
            with open(self.config.TEMP_MEMORY_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []

    def add_temp_memory(self, content: str, role: str = "assistant") -> bool:
        """Add an entry to temp memory."""
        import random
        import string
        from datetime import datetime

        temp_memory = self.get_temp_memory()

        # Generate a random 6-character ID
        random_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))

        # Create a new entry
        new_entry = {
            "id": random_id,
            "content": content,
            "role": role,
            "timestamp": datetime.now().isoformat()  # Use current timestamp
        }

        temp_memory.append(new_entry)

        # Keep only the last 20 entries to prevent it from growing indefinitely
        if len(temp_memory) > 20:
            temp_memory = temp_memory[-20:]

        result = self._save_temp_memory(temp_memory)
        return result

    def delete_temp_memory_item(self, item_id: str) -> bool:
        """Delete an entry from temp memory by ID."""
        temp_memory = self.get_temp_memory()
        original_length = len(temp_memory)

        temp_memory = [item for item in temp_memory if item.get('id') != item_id]

        if len(temp_memory) < original_length:
            return self._save_temp_memory(temp_memory)
        return False

    def clear_temp_memory(self) -> bool:
        """Clear all temp memory."""
        return self._save_temp_memory([])

    def _save_temp_memory(self, temp_memory: List[Dict[str, Any]]) -> bool:
        """Helper method to save temp memory to file."""
        try:
            with open(self.config.TEMP_MEMORY_PATH, 'w', encoding='utf-8') as f:
                json.dump(temp_memory, f, ensure_ascii=False, indent=2)
            # Notify that memory has changed
            self._notify_memory_change()
            return True
        except Exception as e:
            print(f"Error saving temp memory: {e}")
            return False