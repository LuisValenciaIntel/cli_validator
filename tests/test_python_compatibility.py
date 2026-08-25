from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "cli_validator"


def test_backend_sources_parse_with_python_310_grammar() -> None:
    """Prevent accidental use of syntax newer than the supported Python baseline."""
    for source in PACKAGE_ROOT.rglob("*.py"):
        ast.parse(
            source.read_text(encoding="utf-8"),
            filename=str(source),
            feature_version=(3, 10),
        )


def test_backend_does_not_import_datetime_utc() -> None:
    """datetime.UTC was introduced in Python 3.11; use timezone.utc instead."""
    for source in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "datetime":
                assert all(alias.name != "UTC" for alias in node.names), source

