"""Jinja2 HTML report generation."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from cli_validator.models import RunSummary
from cli_validator.reports.base import BaseReportGenerator


class HtmlReportGenerator(BaseReportGenerator):
    """Generate self-contained HTML reports from live or saved evidence."""

    def __init__(self, template_directory: str | Path | None = None) -> None:
        templates = Path(template_directory or Path(__file__).parent / "templates")
        self.environment = Environment(
            loader=FileSystemLoader(templates),
            autoescape=select_autoescape(("html", "xml")),
        )

    def generate(
        self, summary: RunSummary, output_path: str | Path = "results/report.html"
    ) -> Path:
        rows: list[dict[str, Any]] = []
        for outcome in summary.outcomes:
            result = outcome.command_result
            rows.append(
                {
                    "test_id": outcome.case.test_id,
                    "name": outcome.case.name,
                    "status": outcome.status,
                    "skip_reason": outcome.case.skip_reason,
                    "command": outcome.case.command,
                    "execution_time": result.execution_time if result else 0.0,
                    "exit_code": result.exit_code if result else None,
                    "stdout": result.stdout if result else "",
                    "stderr": result.stderr if result else "",
                    "validations": [asdict(item) for item in outcome.validations],
                }
            )
        return self._render(
            {
                "total": summary.total,
                "passed": summary.passed,
                "failed": summary.failed,
                "skipped": summary.skipped,
                "pass_percentage": summary.pass_percentage,
                "started_at": summary.started_at,
                "finished_at": summary.finished_at,
                "inventory": summary.inventory.to_dict(),
                "rows": rows,
            },
            output_path,
        )

    def generate_from_evidence(
        self, results_directory: str | Path = "results", output_path: str | Path | None = None
    ) -> Path:
        results = Path(results_directory).expanduser().resolve()
        rows: list[dict[str, Any]] = []
        for metadata_path in sorted(results.glob("test_*/metadata.json")):
            directory = metadata_path.parent
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            rows.append(
                {
                    **metadata,
                    "stdout": self._read(directory / "stdout.txt"),
                    "stderr": self._read(directory / "stderr.txt"),
                    "validations": json.loads(
                        self._read(directory / "validation.json") or "[]"
                    ),
                }
            )
        if not rows:
            raise FileNotFoundError(f"No test evidence found in {results}")
        passed = sum(row["status"] == "passed" for row in rows)
        failed = sum(row["status"] == "failed" for row in rows)
        skipped = sum(row["status"] == "skipped" for row in rows)
        executed = passed + failed
        return self._render(
            {
                "total": len(rows),
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "pass_percentage": passed / executed * 100 if executed else 0.0,
                "started_at": "Saved evidence",
                "finished_at": "Saved evidence",
                "inventory": {},
                "rows": rows,
            },
            output_path or results / "report.html",
        )

    def _render(self, context: dict[str, Any], output_path: str | Path) -> Path:
        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            self.environment.get_template("report.html.j2").render(**context),
            encoding="utf-8",
        )
        return output

    @staticmethod
    def _read(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

