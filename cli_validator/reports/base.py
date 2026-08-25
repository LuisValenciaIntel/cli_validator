"""Report plugin interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from cli_validator.models import RunSummary


class BaseReportGenerator(ABC):
    """Interface for HTML, JSON or external-system report plugins."""

    @abstractmethod
    def generate(self, summary: RunSummary, output_path: str | Path) -> Path:
        """Generate a report and return its final path."""
