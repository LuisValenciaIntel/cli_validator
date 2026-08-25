"""Command-line interface for CLI Validator."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from cli_validator.config import ConfigurationError
from cli_validator.reports import HtmlReportGenerator
from cli_validator.runner import ValidationRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli-validator",
        description="Discover, execute and validate command-line applications.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    subparsers = parser.add_subparsers(dest="action", required=True)

    run = subparsers.add_parser("run", help="Execute tests from a YAML configuration")
    run.add_argument("config", type=Path)
    run.add_argument("--results", type=Path, default=Path("results"))
    run.add_argument("--no-report", action="store_true")

    discover = subparsers.add_parser("discover", help="Print discovered inventory as JSON")
    discover.add_argument("--config", type=Path)
    discover.add_argument("--output", type=Path)

    validate = subparsers.add_parser("validate", help="Validate and expand YAML without executing")
    validate.add_argument("config", type=Path)

    report = subparsers.add_parser("report", help="Regenerate HTML from saved evidence")
    report.add_argument("--results", type=Path, default=Path("results"))
    report.add_argument("--output", type=Path)

    serve = subparsers.add_parser("serve", help="Start the React web console and API")
    serve.add_argument("--config", type=Path, default=Path("config/commands.yml"))
    serve.add_argument("--results", type=Path, default=Path("results"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.action == "run":
            runner = ValidationRunner(results_directory=args.results)
            summary = runner.run(args.config, generate_report=not args.no_report)
            print(
                f"Total: {summary.total} | Passed: {summary.passed} | "
                f"Failed: {summary.failed} | Skipped: {summary.skipped} | "
                f"Pass: {summary.pass_percentage:.1f}%"
            )
            if not args.no_report:
                print(f"Report: {runner.results_directory / 'report.html'}")
            return 1 if summary.failed else 0

        if args.action == "discover":
            runner = ValidationRunner()
            config = runner.loader.load(args.config) if args.config else None
            payload = json.dumps(runner.discover(config).to_dict(), indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(payload + "\n", encoding="utf-8")
            print(payload)
            return 0

        if args.action == "validate":
            runner = ValidationRunner()
            config = runner.loader.load(args.config)
            inventory = runner.discover(config)
            cases = runner.validate_configuration(args.config, inventory)
            print(f"Configuration valid: {len(cases)} expanded test(s)")
            for case in cases:
                suffix = f" [SKIP: {case.skip_reason}]" if case.skip_reason else ""
                print(f"- {case.test_id}: {case.command}{suffix}")
            return 0

        if args.action == "report":
            output = HtmlReportGenerator().generate_from_evidence(
                args.results, args.output
            )
            print(f"Report: {output}")
            return 0

        if args.action == "serve":
            import uvicorn

            from cli_validator.web import create_app

            uvicorn.run(
                create_app(config_path=args.config, results_directory=args.results),
                host=args.host,
                port=args.port,
            )
            return 0
    except (ConfigurationError, FileNotFoundError, ValueError) as exc:
        logging.error("%s", exc)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())



