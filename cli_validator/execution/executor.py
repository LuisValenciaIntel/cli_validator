"""Reliable subprocess command execution."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from cli_validator.models import CommandResult, RetryPolicy

LOGGER = logging.getLogger(__name__)


class CommandExecutor:
    """Execute shell commands with timing, timeout and retry support."""

    def __init__(
        self,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.runner = runner
        self.sleeper = sleeper

    def execute(
        self,
        command: str,
        *,
        timeout: float = 120,
        retry: RetryPolicy | None = None,
        environment: dict[str, str] | None = None,
        working_directory: str | Path | None = None,
    ) -> CommandResult:
        policy = retry or RetryPolicy()
        cwd = Path(working_directory or Path.cwd()).resolve()
        merged_environment = {**os.environ, **(environment or {})}
        last_result: CommandResult | None = None

        for attempt in range(1, policy.count + 2):
            LOGGER.info("Executing attempt %d/%d: %s", attempt, policy.count + 1, command)
            started = time.perf_counter()
            try:
                completed = self.runner(
                    command,
                    shell=True,
                    cwd=cwd,
                    env=merged_environment,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=timeout,
                    check=False,
                )
                last_result = CommandResult(
                    command=command,
                    stdout=completed.stdout or "",
                    stderr=completed.stderr or "",
                    exit_code=completed.returncode,
                    execution_time=time.perf_counter() - started,
                    attempts=attempt,
                    working_directory=cwd,
                )
            except subprocess.TimeoutExpired as exc:
                last_result = CommandResult(
                    command=command,
                    stdout=self._to_text(exc.stdout),
                    stderr=self._to_text(exc.stderr) or f"Command timed out after {timeout}s",
                    exit_code=124,
                    execution_time=time.perf_counter() - started,
                    attempts=attempt,
                    timed_out=True,
                    working_directory=cwd,
                )
            except OSError as exc:
                last_result = CommandResult(
                    command=command,
                    stdout="",
                    stderr=str(exc),
                    exit_code=127,
                    execution_time=time.perf_counter() - started,
                    attempts=attempt,
                    working_directory=cwd,
                )

            if last_result.exit_code == 0:
                return last_result
            if attempt <= policy.count:
                LOGGER.warning(
                    "Command failed with exit code %d; retrying in %.1fs",
                    last_result.exit_code,
                    policy.delay,
                )
                self.sleeper(policy.delay)

        assert last_result is not None
        return last_result

    @staticmethod
    def _to_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        return value.decode(errors="replace") if isinstance(value, bytes) else value

