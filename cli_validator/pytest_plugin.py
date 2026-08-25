"""Pytest fixtures for executing dynamically expanded CLI tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cli_validator.models import Inventory
from cli_validator.runner import ValidationRunner

INVENTORY_KEY: pytest.StashKey[Inventory] = pytest.StashKey()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("cli-validator")
    group.addoption(
        "--cli-validator-config",
        type=Path,
        help="YAML configuration used to generate cli_validator_case parameters",
    )
    group.addoption(
        "--cli-validator-results",
        type=Path,
        default=Path("results/pytest"),
        help="Evidence directory for pytest-driven CLI tests",
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "cli_validator_case" not in metafunc.fixturenames:
        return
    path = metafunc.config.getoption("--cli-validator-config")
    if path is None:
        metafunc.parametrize(
            "cli_validator_case",
            [pytest.param(None, marks=pytest.mark.skip(reason="No CLI config supplied"))],
        )
        return
    runner = ValidationRunner(
        results_directory=metafunc.config.getoption("--cli-validator-results")
    )
    config = runner.loader.load(path)
    inventory = runner.discover(config)
    cases = runner.validate_configuration(path, inventory)
    metafunc.parametrize(
        "cli_validator_case",
        cases,
        ids=[f"{case.test_id}-{case.name}" for case in cases],
    )
    metafunc.config.stash[INVENTORY_KEY] = inventory


@pytest.fixture
def cli_validator_result(
    request: pytest.FixtureRequest, cli_validator_case: Any
) -> Any:
    """Execute one generated case and return its TestOutcome."""
    runner = ValidationRunner(
        results_directory=request.config.getoption("--cli-validator-results")
    )
    inventory = request.config.stash[INVENTORY_KEY]
    return runner.run_case(cli_validator_case, inventory)


