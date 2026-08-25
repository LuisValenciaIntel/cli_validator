"""Strict, native-type Jinja rendering utilities."""

from __future__ import annotations

from typing import Any

from jinja2 import StrictUndefined, Undefined, UndefinedError
from jinja2.nativetypes import NativeEnvironment


class TemplateRenderer:
    """Render nested configuration values without silently hiding missing data."""

    def __init__(self) -> None:
        self.environment = NativeEnvironment(undefined=StrictUndefined, autoescape=False)

    def render(self, value: Any, context: dict[str, Any]) -> Any:
        if isinstance(value, str):
            rendered = self.environment.from_string(value).render(**context)
            if isinstance(rendered, Undefined):
                raise UndefinedError(str(rendered))
            return rendered
        if isinstance(value, list):
            return [self.render(item, context) for item in value]
        if isinstance(value, dict):
            return {key: self.render(item, context) for key, item in value.items()}
        return value

    def evaluate(self, expression: Any, context: dict[str, Any]) -> bool:
        if isinstance(expression, bool):
            return expression
        if not isinstance(expression, str):
            return bool(expression)
        if "{{" in expression or "{%" in expression:
            return bool(self.render(expression, context))
        return bool(self.environment.compile_expression(expression)(**context))

