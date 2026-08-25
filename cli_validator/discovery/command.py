"""Configurable command-based discovery plugin."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any

from .base import DiscoveryError, DiscoveryProvider


class JsonCommandDiscoveryProvider(DiscoveryProvider):
    """Merge JSON emitted by commands such as ``ipss probehardware``."""

    def __init__(
        self,
        command: str,
        *,
        timeout: float = 30,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.command = command
        self.timeout = timeout
        self.runner = runner

    def discover(self) -> dict[str, Any]:
        try:
            completed = self.runner(
                self.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DiscoveryError(f"Unable to execute {self.command!r}: {exc}") from exc
        if completed.returncode != 0:
            raise DiscoveryError(completed.stderr.strip() or "Discovery command failed")
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DiscoveryError(f"Discovery command did not return valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise DiscoveryError("Discovery JSON root must be an object")
        return value

