from __future__ import annotations

from pathlib import Path

import pytest

from cli_validator.models import CommandResult
from cli_validator.validators import create_default_registry, navigate_json, parse_table


def result(tmp_path: Path, stdout: str = "", stderr: str = "", code: int = 0) -> CommandResult:
    return CommandResult("test", stdout, stderr, code, 0.01, working_directory=tmp_path)


@pytest.mark.parametrize(
    ("definition", "stdout", "stderr", "code", "passed"),
    [
        ({"type": "contains", "value": "PCIe"}, "PCIe device", "", 0, True),
        ({"type": "not_contains", "value": "ERROR", "source": "combined"}, "ok", "", 0, True),
        ({"type": "regex", "value": r"Python 3\.[0-9]+"}, "Python 3.12", "", 0, True),
        ({"type": "exit_code", "value": 2}, "", "", 2, True),
        ({"type": "contains", "value": "pcie", "case_sensitive": False}, "PCIe", "", 0, True),
        (
            {"type": "contains", "value": "Usage: ipss [OPTIONS]", "normalize": True},
            "Usage:\n  ipss [OPTIONS]",
            "",
            0,
            True,
        ),
        ({"type": "empty", "source": "stderr"}, "", "", 0, True),
        ({"type": "empty", "source": "stderr"}, "", "warning", 0, False),
        ({"type": "regex", "value": r"\A\Z", "source": "stderr"}, "", "error", 0, False),
        ({"type": "regex", "value": "["}, "text", "", 0, False),
    ],
)
def test_text_regex_and_exit_validators(
    tmp_path: Path,
    definition: dict[str, object],
    stdout: str,
    stderr: str,
    code: int,
    passed: bool,
) -> None:
    validation = create_default_registry().create(definition).validate(
        result(tmp_path, stdout, stderr, code)
    )
    assert validation.passed is passed


def test_json_validator_navigates_nested_arrays_and_keys(tmp_path: Path) -> None:
    definition = {
        "type": "json",
        "path": "items[0].status.phase",
        "value": "Running",
    }
    validation = create_default_registry().create(definition).validate(
        result(tmp_path, '{"items": [{"status": {"phase": "Running"}}]}')
    )
    assert validation.passed
    assert navigate_json({"a.b": [4]}, '["a.b"][0]') == 4


def test_json_validator_reports_bad_output_instead_of_raising(tmp_path: Path) -> None:
    validation = create_default_registry().create(
        {"type": "json", "path": "items[9]", "value": "x"}
    ).validate(result(tmp_path, "not-json"))
    assert not validation.passed
    assert "JSON validation failed" in validation.message


def test_table_validator_supports_all_any_count_and_pipes(tmp_path: Path) -> None:
    table = "Port     Status\n------------------\n0        Passed\n1        Passed\n"
    registry = create_default_registry()
    assert registry.create(
        {"type": "table", "column": "Status", "value": "Passed", "mode": "all"}
    ).validate(result(tmp_path, table)).passed
    assert registry.create(
        {"type": "table", "column": "Status", "value": "Passed", "mode": "count", "count": 2}
    ).validate(result(tmp_path, table)).passed
    assert registry.create(
        {"type": "table", "column": "Status", "mode": "min_count", "count": 2}
    ).validate(result(tmp_path, table)).passed
    assert registry.create(
        {
            "type": "table",
            "column": "Status",
            "mode": "values",
            "values": ["Passed", "Passed"],
        }
    ).validate(result(tmp_path, table)).passed
    headers, rows = parse_table("| Port | Status |\n|---|---|\n| 0 | Passed |")
    assert headers == ["Port", "Status"]
    assert rows[0]["Status"] == "Passed"


def test_table_validator_parses_ipss_unicode_box_table(tmp_path: Path) -> None:
    table = """\
              List Flows
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Flows                        ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ GNR-PCIe-port0-sc1           │
│ GNR-PCIe-port0-sc1-1rep-1mask│
│ GNR-PCIe-port0-sc1-1rep      │
└──────────────────────────────┘
"""

    headers, rows = parse_table(table)
    validation = create_default_registry().create(
        {"type": "table", "column": "Flows", "mode": "min_count", "count": 2}
    ).validate(result(tmp_path, table))

    assert headers == ["Flows"]
    assert [row["Flows"] for row in rows] == [
        "GNR-PCIe-port0-sc1",
        "GNR-PCIe-port0-sc1-1rep-1mask",
        "GNR-PCIe-port0-sc1-1rep",
    ]
    assert validation.passed


def test_file_exists_is_relative_to_command_directory(tmp_path: Path) -> None:
    (tmp_path / "report.html").write_text("ok", encoding="utf-8")
    validation = create_default_registry().create(
        {"type": "file_exists", "value": "report.html"}
    ).validate(result(tmp_path))
    assert validation.passed


def test_registry_rejects_unknown_validator() -> None:
    with pytest.raises(ValueError, match="Unknown validator"):
        create_default_registry().create({"type": "magic"})



