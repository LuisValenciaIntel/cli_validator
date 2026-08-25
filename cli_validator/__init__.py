"""CLI Validator: extensible command-line application validation framework."""

from cli_validator.models import CommandResult, Inventory, ValidationResult
from cli_validator.runner import ValidationRunner

__version__ = "1.0.0"

__all__ = ["CommandResult", "Inventory", "ValidationResult", "ValidationRunner"]
