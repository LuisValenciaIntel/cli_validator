"""Result validator interfaces and built-ins."""

from .base import BaseValidator, ValidatorRegistry
from .builtin import (
    ContainsValidator,
    ExitCodeValidator,
    FileExistsValidator,
    JsonValidator,
    NotContainsValidator,
    RegexValidator,
    TableValidator,
    create_default_registry,
    navigate_json,
    parse_table,
)

__all__ = [
    "BaseValidator",
    "ContainsValidator",
    "ExitCodeValidator",
    "FileExistsValidator",
    "JsonValidator",
    "NotContainsValidator",
    "RegexValidator",
    "TableValidator",
    "ValidatorRegistry",
    "create_default_registry",
    "navigate_json",
    "parse_table",
]
