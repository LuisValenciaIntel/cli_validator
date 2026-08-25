"""Validator contracts and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from cli_validator.models import CommandResult, ValidationResult


class BaseValidator(ABC):
    """Base class for command-result validators."""

    def __init__(self, **options: Any) -> None:
        self.options = options

    @abstractmethod
    def validate(self, result: CommandResult) -> ValidationResult:
        """Validate a command result without mutating it."""

    def outcome(
        self, passed: bool, message: str, **details: Any
    ) -> ValidationResult:
        return ValidationResult(
            pass_fail=passed,
            message=message,
            validator=self.options.get("type", type(self).__name__),
            details=details,
        )


ValidatorFactory = Callable[..., BaseValidator]


class ValidatorRegistry:
    """Registry used by built-in and external validator plugins."""

    def __init__(self) -> None:
        self._validators: dict[str, type[BaseValidator]] = {}

    def register(
        self, name: str, validator: type[BaseValidator], *, replace: bool = False
    ) -> None:
        key = name.lower()
        if key in self._validators and not replace:
            raise ValueError(f"Validator already registered: {name}")
        self._validators[key] = validator

    def create(self, definition: dict[str, Any]) -> BaseValidator:
        name = str(definition.get("type", "")).lower()
        try:
            validator = self._validators[name]
        except KeyError as exc:
            raise ValueError(f"Unknown validator type: {name or '<missing>'}") from exc
        return validator(**definition)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._validators))
