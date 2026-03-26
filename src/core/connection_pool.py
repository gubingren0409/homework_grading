import time
import logging
import asyncio
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class AllKeysExhaustedError(Exception):
    """Raised when all API keys in the pool are currently in cooldown."""
    pass

class CircuitBreakerKeyPool:
    """
    A thread-safe-ish (asyncio) API key pool with circuit breaker (cooldown) logic.
    Supports dynamic failover and health tracking.
    """
    def __init__(self, name: str, api_keys: List[str]):
        self.name = name
        self.keys_metadata = [
            {"key": k, "status": "HEALTHY", "cooldown_until": 0.0}
            for k in api_keys
        ]
        self._current_index = 0
        logger.info(f"Initialized '{self.name}' pool with {len(api_keys)} keys.")

    def get_key_metadata(self) -> Dict[str, Any]:
        """
        Rotates through keys and returns the first HEALTHY one.
        Clears expired cooldowns on the fly.
        """
        now = time.time()
        num_keys = len(self.keys_metadata)

        for _ in range(num_keys):
            meta = self.keys_metadata[self._current_index]
            
            # Reset expired cooldowns
            if meta["status"] == "COOLDOWN" and now >= meta["cooldown_until"]:
                meta["status"] = "HEALTHY"
                meta["cooldown_until"] = 0.0
                logger.info(f"Key in pool '{self.name}' (index {self._current_index}) has recovered from cooldown.")

            if meta["status"] == "HEALTHY":
                # Advance index for next call (Round-Robin)
                selected_meta = meta
                self._current_index = (self._current_index + 1) % num_keys
                return selected_meta

            self._current_index = (self._current_index + 1) % num_keys

        raise AllKeysExhaustedError(f"All keys in pool '{self.name}' are currently in COOLDOWN.")

    def report_429(self, key: str, cooldown_seconds: int = 60):
        """
        Marks a specific key as COOLDOWN.
        """
        now = time.time()
        for meta in self.keys_metadata:
            if meta["key"] == key:
                meta["status"] = "COOLDOWN"
                meta["cooldown_until"] = now + cooldown_seconds
                logger.warning(f"Key in pool '{self.name}' tripped circuit breaker. Cooling down for {cooldown_seconds}s.")
                break
