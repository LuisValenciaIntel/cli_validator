"""Built-in text, JSON, table and filesystem validators."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..models import CommandResult, ValidationResult
from .base import BaseValidator, ValidatorRegistry


def _selected_output(result: CommandResult, source: str) -> str:
    if source == "stdout":
        return result.stdout
    if source == "stderr":
        return result.stderr
    if source == "combined":
        return result.combined_output
    raise ValueError("source must be stdout, stderr or combined")


def _normalized_text(value: str) -> str:
    """Remove punctuation and whitespace for legacy CLI-output comparisons."""
    return "".join(character for character in value if character.isalnum() or character == "_")


class ContainsValidator(BaseValidator):
    """Check that output contains a configured value."""

    def validate(self, result: CommandResult) -> ValidationResult:
        expected = str(self.options.get("value", ""))
        source = str(self.options.get("source", "stdout"))
        try:
            actual = _selected_output(result, source)
        except ValueError as exc:
            return self.outcome(False, str(exc))
        if self.options.get("normalize", False):
            expected = _normalized_text(expected)
            actual = _normalized_text(actual)
        case_sensitive = bool(self.options.get("case_sensitive", True))
        found = expected in actual if case_sensitive else expected.casefold() in actual.casefold()
        return self.outcome(found, f"Expected {expected!r} to be present in {source}")


class NotContainsValidator(BaseValidator):
    """Check that output does not contain a configured value."""

    def validate(self, result: CommandResult) -> ValidationResult:
        expected = str(self.options.get("value", ""))
        source = str(self.options.get("source", "stdout"))
        try:
            actual = _selected_output(result, source)
        except ValueError as exc:
            return self.outcome(False, str(exc))
        case_sensitive = bool(self.options.get("case_sensitive", True))
        found = expected in actual if case_sensitive else expected.casefold() in actual.casefold()
        return self.outcome(not found, f"Expected {expected!r} to be absent from {source}")


class EmptyValidator(BaseValidator):
    """Check that the selected output stream is exactly empty."""

    def validate(self, result: CommandResult) -> ValidationResult:
        source = str(self.options.get("source", "stdout"))
        try:
            actual = _selected_output(result, source)
        except ValueError as exc:
            return self.outcome(False, str(exc))
        return self.outcome(
            actual == "",
            f"Expected {source} to be empty; got {len(actual)} character(s)",
            source=source,
            length=len(actual),
        )


class RegexValidator(BaseValidator):
    """Search output using a Python regular expression."""

    def validate(self, result: CommandResult) -> ValidationResult:
        pattern = str(self.options.get("value", ""))
        source = str(self.options.get("source", "stdout"))
        try:
            actual = _selected_output(result, source)
            flags = re.MULTILINE | (re.IGNORECASE if self.options.get("ignore_case") else 0)
            matched = re.search(pattern, actual, flags) is not None
        except (ValueError, re.error) as exc:
            return self.outcome(False, f"Invalid regex validation: {exc}")
        return self.outcome(matched, f"Expected pattern {pattern!r} in {source}")


class ExitCodeValidator(BaseValidator):
    """Compare the process exit code."""

    def validate(self, result: CommandResult) -> ValidationResult:
        try:
            expected = int(self.options.get("value", 0))
        except (TypeError, ValueError):
            return self.outcome(False, "Exit-code value must be an integer")
        return self.outcome(
            result.exit_code == expected,
            f"Expected exit code {expected}; got {result.exit_code}",
            expected=expected,
            actual=result.exit_code,
        )


_JSON_TOKEN = re.compile(
    r"(?:^|\.)([^][.]+)|\[(?:(\d+)|\"([^\"]+)\"|'([^']+)')\]"
)


def navigate_json(value: Any, path: str) -> Any:
    """Navigate dotted/bracket JSON paths such as ``items[0].status.phase``."""
    if not path:
        return value
    position = 0
    current = value
    for match in _JSON_TOKEN.finditer(path):
        if match.start() != position:
            raise KeyError(f"Invalid JSON path near {path[position:]!r}")
        position = match.end()
        index, quoted_double, quoted_single = match.group(2, 3, 4)
        key = match.group(1) or quoted_double or quoted_single
        if index is not None:
            if not isinstance(current, list):
                raise TypeError(f"Expected a list before index {index}")
            current = current[int(index)]
        else:
            if not isinstance(current, dict):
                raise TypeError(f"Expected an object before key {key!r}")
            current = current[key]
    if position != len(path):
        raise KeyError(f"Invalid JSON path near {path[position:]!r}")
    return current


class JsonValidator(BaseValidator):
    """Parse JSON and compare the value at a configured path."""

    def validate(self, result: CommandResult) -> ValidationResult:
        source = str(self.options.get("source", "stdout"))
        path = str(self.options.get("path", ""))
        expected = self.options.get("value")
        try:
            payload = json.loads(_selected_output(result, source))
            actual = navigate_json(payload, path)
        except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError) as exc:
            return self.outcome(False, f"JSON validation failed at {path!r}: {exc}")
        return self.outcome(
            actual == expected,
            f"Expected JSON value {expected!r} at {path!r}; got {actual!r}",
            expected=expected,
            actual=actual,
            path=path,
        )


_VERTICAL_TABLE_BORDERS = "|│┃║"


def _is_table_border(line: str) -> bool:
    return all(
        character.isspace()
        or character in "-+|:="
        or "\u2500" <= character <= "\u257f"
        for character in line
    )


def _is_boxed_table_row(line: str) -> bool:
    stripped = line.strip()
    return (
        len(stripped) >= 2
        and stripped[0] in _VERTICAL_TABLE_BORDERS
        and stripped[-1] in _VERTICAL_TABLE_BORDERS
        and not _is_table_border(stripped)
    )


def parse_table(output: str) -> tuple[list[str], list[dict[str, str]]]:
    """Parse Rich/Unicode, pipe-delimited or 2+-space-delimited text tables."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    boxed_rows = [line for line in lines if _is_boxed_table_row(line)]
    if boxed_rows:
        lines = boxed_rows
        pipe_delimited = True
    else:
        lines = [line for line in lines if not _is_table_border(line)]
        pipe_delimited = bool(lines and "|" in lines[0])
    if not lines:
        raise ValueError("Output contains no table")

    def split(line: str) -> list[str]:
        if pipe_delimited:
            stripped = line.strip().strip(_VERTICAL_TABLE_BORDERS)
            return [cell.strip() for cell in re.split(r"[|│┃║]", stripped)]
        return [cell.strip() for cell in re.split(r"\s{2,}", line.strip())]

    headers = split(lines[0])
    if not headers or any(not header for header in headers):
        raise ValueError("Table header is empty")
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        cells = split(line)
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells, strict=True)))
    return headers, rows


class TableValidator(BaseValidator):
    """Validate that table rows contain expected column values."""

    def validate(self, result: CommandResult) -> ValidationResult:
        source = str(self.options.get("source", "stdout"))
        column = str(self.options.get("column", ""))
        expected = str(self.options.get("value", ""))
        mode = str(self.options.get("mode", "all")).lower()
        try:
            _, rows = parse_table(_selected_output(result, source))
            if not rows:
                raise ValueError("Table contains no data rows")
            if column not in rows[0]:
                raise ValueError(f"Unknown table column {column!r}")
            actual_values = [row[column] for row in rows]
            if self.options.get("normalize", False):
                expected = _normalized_text(expected)
                actual_values = [_normalized_text(value) for value in actual_values]
            matches = sum(value == expected for value in actual_values)
            if mode == "all":
                passed = matches == len(rows)
            elif mode == "any":
                passed = matches > 0
            elif mode == "count":
                expected_count = int(self.options["count"])
                passed = matches == expected_count
            elif mode == "min_count":
                expected_count = int(self.options["count"])
                passed = len(rows) >= expected_count
            elif mode == "values":
                configured_values = self.options["values"]
                if not isinstance(configured_values, list):
                    raise ValueError("Table values mode requires a values list")
                expected_values = [str(value) for value in configured_values]
                if self.options.get("normalize", False):
                    expected_values = [_normalized_text(value) for value in expected_values]
                passed = sorted(actual_values) == sorted(expected_values)
            else:
                raise ValueError("Table mode must be all, any, count, min_count or values")
        except (TypeError, ValueError, KeyError) as exc:
            return self.outcome(False, f"Table validation failed: {exc}")
        return self.outcome(
            passed,
            f"Matched {matches} of {len(rows)} rows in column {column!r}",
            matches=matches,
            rows=len(rows),
        )


class FileExistsValidator(BaseValidator):
    """Check for a file relative to the command working directory."""

    def validate(self, result: CommandResult) -> ValidationResult:
        configured = Path(str(self.options.get("value", ""))).expanduser()
        path = configured if configured.is_absolute() else result.working_directory / configured
        exists = path.is_file()
        return self.outcome(exists, f"Expected file to exist: {path}", path=str(path))


def create_default_registry() -> ValidatorRegistry:
    """Create an independent registry containing all built-in validators."""
    registry = ValidatorRegistry()
    registry.register("contains", ContainsValidator)
    registry.register("not_contains", NotContainsValidator)
    registry.register("empty", EmptyValidator)
    registry.register("regex", RegexValidator)
    registry.register("exit_code", ExitCodeValidator)
    registry.register("json", JsonValidator)
    registry.register("table", TableValidator)
    registry.register("file_exists", FileExistsValidator)
    return registry





