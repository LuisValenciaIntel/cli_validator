"""Discovery plugin interfaces and orchestration."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from ..models import Inventory

LOGGER = logging.getLogger(__name__)


class DiscoveryError(RuntimeError):
    """Raised when a required discovery provider cannot complete."""


class DiscoveryProvider(ABC):
    """Interface implemented by environment discovery plugins."""

    @abstractmethod
    def discover(self) -> dict[str, Any]:
        """Discover and return inventory facts."""


class DiscoveryEngine:
    """Execute providers and merge their output into one inventory."""

    def __init__(
        self, providers: list[DiscoveryProvider] | None = None, *, strict: bool = False
    ) -> None:
        self.providers = providers or []
        self.strict = strict

    def register(self, provider: DiscoveryProvider) -> None:
        self.providers.append(provider)

    def discover(self) -> Inventory:
        inventory = Inventory()
        for provider in self.providers:
            try:
                inventory.merge(provider.discover())
            except Exception as exc:  # providers are third-party extension points
                message = f"Discovery provider {type(provider).__name__} failed: {exc}"
                if self.strict:
                    raise DiscoveryError(message) from exc
                LOGGER.warning(message)
        return inventory

