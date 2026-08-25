"""Framework entities with JSON-serializable representations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class RetryPolicy:
    """Retry settings; count is the number of retries after the first attempt."""

    count: int = 0
    delay: float = 0.0


@dataclass(slots=True)
class CommandResult:
    """Result captured from a command invocation."""

    command: str
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
    attempts: int = 1
    timed_out: bool = False
    working_directory: Path = field(default_factory=Path.cwd)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def combined_output(self) -> str:
        """Return stdout and stderr as one searchable string."""
        return "\n".join(part for part in (self.stdout, self.stderr) if part)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["working_directory"] = str(self.working_directory)
        return data


@dataclass(slots=True)
class ValidationResult:
    """Outcome returned by every validator."""

    pass_fail: bool
    message: str
    validator: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.pass_fail


@dataclass(slots=True)
class Inventory:
    """Dynamically discovered platform facts."""

    platform: str = "unknown"
    devices: list[str] = field(default_factory=list)
    capabilities: dict[str, bool] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)

    def merge(self, discovered: dict[str, Any]) -> None:
        """Merge provider output while preserving provider-specific fields."""
        if "platform" in discovered:
            self.platform = str(discovered["platform"])
        if "devices" in discovered:
            self.devices = list(dict.fromkeys([*self.devices, *discovered["devices"]]))
        self.capabilities.update(discovered.get("capabilities", {}))
        for key, value in discovered.items():
            if key not in {"platform", "devices", "capabilities"}:
                self.data[key] = value

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "devices": self.devices,
            "capabilities": self.capabilities,
            **self.data,
        }


@dataclass(slots=True)
class TestCase:
    """A fully expanded, executable test definition."""

    test_id: str
    name: str
    command: str
    validations: list[dict[str, Any]] = field(default_factory=list)
    timeout: float = 120.0
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    environment: dict[str, str] = field(default_factory=dict)
    working_directory: Path = field(default_factory=Path.cwd)
    skip_reason: str | None = None


@dataclass(slots=True)
class TestOutcome:
    """Execution, validation and evidence state for one test."""

    case: TestCase
    status: str
    command_result: CommandResult | None = None
    validations: list[ValidationResult] = field(default_factory=list)
    evidence_directory: Path | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed"


@dataclass(slots=True)
class RunSummary:
    """Aggregate results for one framework run."""

    outcomes: list[TestOutcome]
    inventory: Inventory
    started_at: str
    finished_at: str

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def passed(self) -> int:
        return sum(outcome.status == "passed" for outcome in self.outcomes)

    @property
    def failed(self) -> int:
        return sum(outcome.status == "failed" for outcome in self.outcomes)

    @property
    def skipped(self) -> int:
        return sum(outcome.status == "skipped" for outcome in self.outcomes)

    @property
    def pass_percentage(self) -> float:
        executed = self.passed + self.failed
        return (self.passed / executed * 100.0) if executed else 0.0



