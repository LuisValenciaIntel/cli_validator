"""Linux PCIe discovery provider."""

from __future__ import annotations

import os
import re
import socket
import subprocess
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from .base import DiscoveryError, DiscoveryProvider

_BDF_LINE = re.compile(
    r"^(?P<bdf>(?:[0-9a-fA-F]{4}:)?[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7])\s+"
)
_SPEED = re.compile(r"(?:LnkCap|LnkSta):.*?Speed\s+(?P<speed>[0-9.]+)GT/s")
_SPEED_TO_GENERATION = {2.5: 1, 5.0: 2, 8.0: 3, 16.0: 4, 32.0: 5, 64.0: 6}


class PCIeDiscoveryProvider(DiscoveryProvider):
    """Discover PCI functions and link generations using ``lspci``."""

    def __init__(
        self,
        command: tuple[str, ...] = ("lspci", "-D", "-vv"),
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.command = command
        self.runner = runner

    def discover(self) -> dict[str, Any]:
        try:
            completed = self.runner(
                self.command,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DiscoveryError(f"Unable to execute {' '.join(self.command)}: {exc}") from exc
        if completed.returncode != 0:
            raise DiscoveryError(completed.stderr.strip() or "lspci returned a failure")

        devices: list[str] = []
        generations: dict[int, list[str]] = defaultdict(list)
        current_bdf: str | None = None
        best_speed: float | None = None

        def finish_device() -> None:
            if current_bdf is not None and best_speed is not None:
                generation = _SPEED_TO_GENERATION.get(best_speed)
                if generation:
                    generations[generation].append(current_bdf)

        for line in completed.stdout.splitlines():
            match = _BDF_LINE.match(line)
            if match:
                finish_device()
                bdf = match.group("bdf")
                if bdf.count(":") == 1:
                    bdf = f"0000:{bdf}"
                current_bdf = bdf
                devices.append(bdf)
                best_speed = None
                continue
            speed_match = _SPEED.search(line)
            if current_bdf and speed_match:
                speed = float(speed_match.group("speed"))
                best_speed = max(best_speed or speed, speed)
        finish_device()

        generation_data = {
            f"gen{generation}_devices": values
            for generation, values in sorted(generations.items())
        }
        capabilities = {
            f"pcie_gen{generation}": bool(generations.get(generation))
            for generation in range(1, 7)
        }
        capabilities.update(
            {
                "target_mode": os.getenv("IPSS_TARGET_MODE", "").lower()
                in {"1", "true", "yes"},
                "iomt": os.getenv("IPSS_IOMT", "").lower() in {"1", "true", "yes"},
            }
        )
        return {
            "platform": os.getenv("IPSS_PLATFORM", socket.gethostname()),
            "devices": devices,
            "pcie_devices": devices,
            **generation_data,
            "capabilities": capabilities,
        }



