from __future__ import annotations

import json
import subprocess
from pathlib import Path

from cli_validator.cli import main
from cli_validator.execution import CommandExecutor
from cli_validator.models import CommandResult, Inventory, RetryPolicy
from cli_validator.runner import ValidationRunner


def test_executor_retries_failures_and_captures_last_result(tmp_path: Path) -> None:
    calls = 0
    sleeps: list[float] = []

    def process(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        code = 1 if calls == 1 else 0
        return subprocess.CompletedProcess(args[0], code, "ready" if not code else "", "failed")

    result = CommandExecutor(process, sleeps.append).execute(
        "tool status",
        timeout=3,
        retry=RetryPolicy(count=2, delay=0.25),
        working_directory=tmp_path,
    )

    assert result.exit_code == 0
    assert result.attempts == 2
    assert result.stdout == "ready"
    assert sleeps == [0.25]


def test_executor_converts_timeout_to_evidence(tmp_path: Path) -> None:
    def process(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("slow", 1, output=b"partial")

    result = CommandExecutor(process, lambda _: None).execute(
        "slow", timeout=1, working_directory=tmp_path
    )
    assert result.exit_code == 124
    assert result.timed_out
    assert result.stdout == "partial"


class FakeExecutor(CommandExecutor):
    def execute(self, command: str, **kwargs: object) -> CommandResult:
        return CommandResult(
            command=command,
            stdout="PCIe device ready",
            stderr="",
            exit_code=0,
            execution_time=0.05,
            working_directory=Path(str(kwargs["working_directory"])),
        )


def test_runner_writes_complete_evidence_and_html_report(tmp_path: Path) -> None:
    config = tmp_path / "commands.yml"
    config.write_text(
        """
tests:
  - name: Show device
    foreach: pcie_devices
    command: show {{ item }}
    validations:
      - {type: contains, value: PCIe}
      - {type: exit_code, value: 0}
  - name: Unsupported capability
    requires: [target_mode]
    command: target status
""",
        encoding="utf-8",
    )
    inventory = Inventory(
        platform="OKS",
        capabilities={"target_mode": False},
        data={"pcie_devices": ["0000:6a:00.0"]},
    )
    results = tmp_path / "results"
    summary = ValidationRunner(
        executor=FakeExecutor(), results_directory=results
    ).run(config, inventory=inventory)

    assert (summary.total, summary.passed, summary.failed, summary.skipped) == (2, 1, 0, 1)
    evidence = results / "test_001"
    assert (evidence / "command.txt").read_text(encoding="utf-8").strip() == "show 0000:6a:00.0"
    metadata = json.loads((evidence / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["platform"] == "OKS"
    assert metadata["execution_time"] == 0.05
    assert "CLI Validator Report" in (results / "report.html").read_text(encoding="utf-8")

    regenerated = results / "regenerated.html"
    assert main(["report", "--results", str(results), "--output", str(regenerated)]) == 0
    assert regenerated.is_file()


def test_validate_cli_rejects_unknown_validator_before_execution(tmp_path: Path) -> None:
    config = tmp_path / "commands.yml"
    config.write_text(
        "tests: [{command: echo ok, validations: [{type: unknown}]}]\n",
        encoding="utf-8",
    )
    assert main(["validate", str(config)]) == 2
