"""YAML configuration loading and dynamic test expansion."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jinja2 import UndefinedError

from cli_validator.models import Inventory, RetryPolicy, TestCase
from cli_validator.utils import TemplateRenderer


class ConfigurationError(ValueError):
    """Raised for an invalid or unrenderable configuration."""


@dataclass(slots=True)
class FrameworkConfig:
    """Validated top-level YAML configuration."""

    source: Path
    variables: dict[str, Any] = field(default_factory=dict)
    tests: list[dict[str, Any]] = field(default_factory=list)
    discovery: list[dict[str, Any]] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)


class ConfigLoader:
    """Load safe YAML and validate its structural contract."""

    def load(self, path: str | Path) -> FrameworkConfig:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise ConfigurationError(f"Configuration file does not exist: {source}")
        try:
            raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"Invalid YAML in {source}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigurationError("Configuration root must be a mapping")
        tests = raw.get("tests", [])
        if not isinstance(tests, list):
            raise ConfigurationError("'tests' must be a list")
        for index, test in enumerate(tests, 1):
            if not isinstance(test, dict) or not isinstance(test.get("command"), str):
                raise ConfigurationError(f"Test {index} must be a mapping with a command")
            validations = test.get("validations", [])
            if not isinstance(validations, list):
                raise ConfigurationError(f"Test {index} validations must be a list")
            for validation in validations:
                if not isinstance(validation, dict) or "type" not in validation:
                    raise ConfigurationError(
                        f"Test {index} contains a validation without a type"
                    )
        variables = raw.get("variables", {})
        defaults = raw.get("defaults", {})
        discovery = raw.get("discovery", [])
        if not isinstance(variables, dict) or not isinstance(defaults, dict):
            raise ConfigurationError("'variables' and 'defaults' must be mappings")
        if not isinstance(discovery, list):
            raise ConfigurationError("'discovery' must be a list")
        return FrameworkConfig(source, variables, tests, discovery, defaults)


class TestExpander:
    """Resolve variables, conditions and foreach definitions into test cases."""

    def __init__(self, renderer: TemplateRenderer | None = None) -> None:
        self.renderer = renderer or TemplateRenderer()

    def build_context(
        self, config: FrameworkConfig, inventory: Inventory
    ) -> dict[str, Any]:
        inventory_data = inventory.to_dict()
        context: dict[str, Any] = {
            "env": dict(os.environ),
            "inventory": inventory_data,
            "defaults": config.defaults,
            "_config_directory": config.source.parent,
            **inventory_data,
        }
        unresolved = dict(config.variables)
        for _ in range(len(unresolved) + 1):
            progress = False
            for key, value in list(unresolved.items()):
                try:
                    context[key] = self.renderer.render(value, context)
                except UndefinedError:
                    continue
                del unresolved[key]
                progress = True
            if not unresolved or not progress:
                break
        if unresolved:
            raise ConfigurationError(
                "Unable to resolve variables: " + ", ".join(sorted(unresolved))
            )
        return context

    def expand(self, config: FrameworkConfig, inventory: Inventory) -> list[TestCase]:
        context = self.build_context(config, inventory)
        cases: list[TestCase] = []
        serial = 1
        for definition in config.tests:
            items: list[Any] = [None]
            has_foreach = "foreach" in definition
            if has_foreach:
                foreach = definition["foreach"]
                expression = foreach if "{{" in str(foreach) else "{{ " + str(foreach) + " }}"
                try:
                    resolved = self.renderer.render(expression, context)
                except UndefinedError as exc:
                    raise ConfigurationError(f"Unknown foreach value {foreach!r}: {exc}") from exc
                if not isinstance(resolved, (list, tuple)):
                    raise ConfigurationError(f"foreach {foreach!r} must resolve to a list")
                items = list(resolved)
                if not items:
                    cases.append(
                        self._create_case(
                            definition,
                            context,
                            serial,
                            skip_reason=f"foreach {foreach!r} resolved to no items",
                            render=False,
                        )
                    )
                    serial += 1
                    continue

            for item in items:
                item_context = {**context, "item": item}
                skip_reason = self._skip_reason(definition, item_context)
                cases.append(
                    self._create_case(definition, item_context, serial, skip_reason)
                )
                serial += 1
        return cases

    def _skip_reason(
        self, definition: dict[str, Any], context: dict[str, Any]
    ) -> str | None:
        capabilities = context.get("capabilities", {})
        requirements = definition.get("requires", [])
        if isinstance(requirements, str):
            requirements = [requirements]
        if isinstance(requirements, list):
            missing = [name for name in requirements if not capabilities.get(name, False)]
        elif isinstance(requirements, dict):
            missing = [
                name for name, expected in requirements.items()
                if capabilities.get(name) != expected
            ]
        else:
            raise ConfigurationError("requires must be a string, list or mapping")
        if missing:
            return "Missing required capabilities: " + ", ".join(missing)
        if "when" in definition and not self.renderer.evaluate(definition["when"], context):
            return f"Condition not met: {definition['when']}"
        return None

    def _create_case(
        self,
        definition: dict[str, Any],
        context: dict[str, Any],
        serial: int,
        skip_reason: str | None = None,
        *,
        render: bool = True,
    ) -> TestCase:
        rendered = self.renderer.render(definition, context) if render else definition
        defaults = self.renderer.render(context.get("defaults", {}), context)
        merged = {**defaults, **rendered}
        retry_raw = {**defaults.get("retry", {}), **rendered.get("retry", {})}
        if not isinstance(retry_raw, dict):
            raise ConfigurationError("retry must be a mapping")
        try:
            timeout = float(merged.get("timeout", context.get("timeout", 120)))
            retry = RetryPolicy(
                count=int(retry_raw.get("count", 0)),
                delay=float(retry_raw.get("delay", 0)),
            )
        except (TypeError, ValueError) as exc:
            raise ConfigurationError("timeout and retry values must be numeric") from exc
        if timeout <= 0 or retry.count < 0 or retry.delay < 0:
            raise ConfigurationError("timeout must be positive; retry values cannot be negative")
        cwd = Path(
            str(merged.get("working_directory", context["_config_directory"]))
        ).expanduser()
        if not cwd.is_absolute():
            cwd = context["_config_directory"] / cwd
        environment = {
            **defaults.get("environment", {}),
            **rendered.get("environment", {}),
        }
        if not isinstance(environment, dict):
            raise ConfigurationError("environment must be a mapping")
        check_stderr = merged.get("check_stderr", False)
        if not isinstance(check_stderr, bool):
            raise ConfigurationError("check_stderr must be true or false")
        validations = list(merged.get("validations", []))
        if check_stderr:
            validations.append({"type": "empty", "source": "stderr"})
        return TestCase(
            test_id=f"test_{serial:03d}",
            name=str(merged.get("name", f"Test {serial}")),
            command=str(merged["command"]),
            validations=validations,
            timeout=timeout,
            retry=retry,
            environment={str(key): str(value) for key, value in environment.items()},
            working_directory=cwd.resolve(),
            skip_reason=skip_reason,
        )

