from __future__ import annotations

from pathlib import Path

import pytest

from cli_validator.config import ConfigLoader, ConfigurationError
from cli_validator.config import TestExpander as DynamicTestExpander
from cli_validator.models import Inventory


def write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "commands.yml"
    path.write_text(content, encoding="utf-8")
    return path


def test_variables_foreach_defaults_environment_and_capability_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLI_EXPECTED", "PCIe")
    path = write_config(
        tmp_path,
        """
variables:
  timeout: 9
  expected: "{{ env.CLI_EXPECTED }}"
defaults:
  retry: {count: 2, delay: 0.5}
  environment: {COMMON: yes}
tests:
  - name: Device {{ item }}
    foreach: pcie_devices
    requires: [pcie_gen6]
    command: tool show {{ item }}
    environment: {LOCAL: value}
    validations:
      - {type: contains, value: "{{ expected }}"}
  - name: Target-only
    requires: {target_mode: true}
    command: tool target
""",
    )
    inventory = Inventory(
        platform="OKS",
        devices=["0000:6a:00.0", "0000:af:00.0"],
        capabilities={"pcie_gen6": True, "target_mode": False},
        data={"pcie_devices": ["0000:6a:00.0", "0000:af:00.0"]},
    )

    cases = DynamicTestExpander().expand(ConfigLoader().load(path), inventory)

    assert [case.command for case in cases[:2]] == [
        "tool show 0000:6a:00.0",
        "tool show 0000:af:00.0",
    ]
    assert cases[0].timeout == 9
    assert cases[0].retry.count == 2
    assert cases[0].environment == {"COMMON": "True", "LOCAL": "value"}
    assert cases[0].working_directory == tmp_path
    assert cases[2].skip_reason == "Missing required capabilities: target_mode"


def test_empty_foreach_becomes_visible_skipped_test(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "tests:\n  - foreach: pcie_devices\n    command: show {{ item }}\n",
    )
    inventory = Inventory(data={"pcie_devices": []})
    cases = DynamicTestExpander().expand(ConfigLoader().load(path), inventory)
    assert len(cases) == 1
    assert "resolved to no items" in (cases[0].skip_reason or "")


def test_loader_rejects_invalid_test_shape(tmp_path: Path) -> None:
    path = write_config(tmp_path, "tests:\n  - name: missing command\n")
    with pytest.raises(ConfigurationError, match="with a command"):
        ConfigLoader().load(path)


def test_unresolved_variable_is_an_actionable_error(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "variables: {bad: '{{ missing }}'}\ntests: [{command: echo ok}]\n",
    )
    with pytest.raises(ConfigurationError, match="bad"):
        DynamicTestExpander().expand(ConfigLoader().load(path), Inventory())


def test_project_example_config_expands() -> None:
    path = Path(__file__).parents[1] / "config" / "commands.yml"

    cases = DynamicTestExpander().expand(ConfigLoader().load(path), Inventory())

    assert cases
    assert any(case.command == "ipss listflows" for case in cases)


def test_check_stderr_is_opt_in_and_must_be_boolean(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
tests:
  - name: Warnings allowed
    command: tool warning
  - name: Empty stderr required
    command: tool strict
    check_stderr: true
""",
    )

    cases = DynamicTestExpander().expand(ConfigLoader().load(path), Inventory())

    assert cases[0].validations == []
    assert cases[1].validations == [{"type": "empty", "source": "stderr"}]

    invalid = write_config(
        tmp_path,
        'tests:\n  - command: tool\n    check_stderr: "yes"\n',
    )
    with pytest.raises(ConfigurationError, match="check_stderr must be true or false"):
        DynamicTestExpander().expand(ConfigLoader().load(invalid), Inventory())



