"""High-level test run orchestration."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from cli_validator.config import ConfigLoader, FrameworkConfig, TestExpander
from cli_validator.discovery import (
    DiscoveryEngine,
    DiscoveryProvider,
    JsonCommandDiscoveryProvider,
    PCIeDiscoveryProvider,
)
from cli_validator.execution import CommandExecutor
from cli_validator.inventory import EvidenceCollector
from cli_validator.models import Inventory, RunSummary, TestCase, TestOutcome, ValidationResult
from cli_validator.reports import HtmlReportGenerator
from cli_validator.validators import ValidatorRegistry, create_default_registry

LOGGER = logging.getLogger(__name__)


class ValidationRunner:
    """Coordinate discovery, expansion, execution, validation and reporting."""

    def __init__(
        self,
        *,
        executor: CommandExecutor | None = None,
        validators: ValidatorRegistry | None = None,
        results_directory: str | Path = "results",
    ) -> None:
        self.executor = executor or CommandExecutor()
        self.validators = validators or create_default_registry()
        self.results_directory = Path(results_directory).expanduser().resolve()
        self.evidence = EvidenceCollector(self.results_directory)
        self.reporter = HtmlReportGenerator()
        self.loader = ConfigLoader()
        self.expander = TestExpander()

    def discover(self, config: FrameworkConfig | None = None) -> Inventory:
        definitions = config.discovery if config else []
        providers: list[DiscoveryProvider] = []
        if not definitions:
            providers.append(PCIeDiscoveryProvider())
        for definition in definitions:
            if not isinstance(definition, dict):
                raise ValueError("Each discovery definition must be a mapping")
            provider_type = str(definition.get("type", "pcie"))
            if provider_type == "pcie":
                providers.append(PCIeDiscoveryProvider())
            elif provider_type == "json_command":
                if not definition.get("command"):
                    raise ValueError("json_command discovery requires a command")
                providers.append(
                    JsonCommandDiscoveryProvider(
                        str(definition["command"]),
                        timeout=float(definition.get("timeout", 30)),
                    )
                )
            else:
                raise ValueError(f"Unknown discovery provider: {provider_type}")
        return DiscoveryEngine(providers).discover()

    def validate_configuration(
        self, path: str | Path, inventory: Inventory | None = None
    ) -> list[TestCase]:
        config = self.loader.load(path)
        cases = self.expander.expand(config, inventory or Inventory())
        for case in cases:
            for definition in case.validations:
                self.validators.create(definition)
        return cases

    def run(
        self,
        path: str | Path,
        *,
        inventory: Inventory | None = None,
        generate_report: bool = True,
    ) -> RunSummary:
        started = datetime.now(timezone.utc).isoformat()
        config = self.loader.load(path)
        discovered = inventory or self.discover(config)
        cases = self.expander.expand(config, discovered)
        outcomes = [self.run_case(case, discovered) for case in cases]
        summary = RunSummary(
            outcomes=outcomes,
            inventory=discovered,
            started_at=started,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        if generate_report:
            self.reporter.generate(summary, self.results_directory / "report.html")
        return summary

    def run_case(self, case: TestCase, inventory: Inventory) -> TestOutcome:
        if case.skip_reason:
            outcome = TestOutcome(case=case, status="skipped")
            self.evidence.collect(outcome, inventory)
            return outcome

        result = self.executor.execute(
            case.command,
            timeout=case.timeout,
            retry=case.retry,
            environment=case.environment,
            working_directory=case.working_directory,
        )
        validations: list[ValidationResult] = []
        for definition in case.validations:
            try:
                validations.append(self.validators.create(definition).validate(result))
            except Exception as exc:  # plugin failures become test evidence
                LOGGER.exception("Validator plugin failed")
                validations.append(
                    ValidationResult(
                        False,
                        f"Validator {definition.get('type', '<missing>')} failed: {exc}",
                        str(definition.get("type", "unknown")),
                    )
                )
        passed = result.exit_code == 0 and all(item.passed for item in validations)
        outcome = TestOutcome(
            case=case,
            status="passed" if passed else "failed",
            command_result=result,
            validations=validations,
        )
        self.evidence.collect(outcome, inventory)
        return outcome




