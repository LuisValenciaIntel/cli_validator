"""Filesystem evidence collection."""

from __future__ import annotations

import getpass
import json
import platform
import socket
from dataclasses import asdict
from pathlib import Path
from typing import Any

from cli_validator.models import Inventory, TestOutcome


class EvidenceCollector:
    """Persist command output, metadata and validation results per test."""

    def __init__(self, results_directory: str | Path = "results") -> None:
        self.results_directory = Path(results_directory).expanduser().resolve()
        self.results_directory.mkdir(parents=True, exist_ok=True)

    def collect(self, outcome: TestOutcome, inventory: Inventory) -> Path:
        directory = self.results_directory / outcome.case.test_id
        directory.mkdir(parents=True, exist_ok=True)
        result = outcome.command_result
        self._write_text(directory / "command.txt", outcome.case.command + "\n")
        self._write_text(directory / "stdout.txt", result.stdout if result else "")
        self._write_text(directory / "stderr.txt", result.stderr if result else "")
        metadata: dict[str, Any] = {
            "test_id": outcome.case.test_id,
            "name": outcome.case.name,
            "status": outcome.status,
            "skip_reason": outcome.case.skip_reason,
            "command": outcome.case.command,
            "hostname": socket.gethostname(),
            "platform": inventory.platform or platform.platform(),
            "system": platform.platform(),
            "user": getpass.getuser(),
            "timestamp": result.timestamp if result else None,
            "execution_time": result.execution_time if result else 0.0,
            "exit_code": result.exit_code if result else None,
            "attempts": result.attempts if result else 0,
            "timed_out": result.timed_out if result else False,
        }
        self._write_json(directory / "metadata.json", metadata)
        self._write_json(
            directory / "validation.json",
            [asdict(validation) for validation in outcome.validations],
        )
        outcome.evidence_directory = directory
        return directory

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8", errors="replace")

    @staticmethod
    def _write_json(path: Path, content: Any) -> None:
        path.write_text(
            json.dumps(content, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
