"""Thread-safe execution management and WebSocket event delivery."""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cli_validator.models import RunSummary, TestCase, TestOutcome
from cli_validator.runner import ValidationRunner


class EventBroker:
    """Fan out run events to independent bounded asyncio queues."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the broker to the API event loop."""
        self._loop = loop

    def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Subscribe to events for one run."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        for event in self._history.get(run_id, ()):
            queue.put_nowait(event)
        self._subscribers.setdefault(run_id, set()).add(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a run subscription."""
        subscribers = self._subscribers.get(run_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(run_id, None)

    def publish_from_thread(self, run_id: str, event: dict[str, Any]) -> None:
        """Schedule an event safely from a worker thread."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._publish_now, run_id, event)

    def _publish_now(self, run_id: str, event: dict[str, Any]) -> None:
        payload = {"run_id": run_id, "timestamp": _timestamp(), **event}
        history = self._history.setdefault(run_id, [])
        history.append(payload)
        del history[:-500]
        for queue in tuple(self._subscribers.get(run_id, ())):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(payload)


@dataclass(slots=True)
class RunRecord:
    """Mutable state for one background validation run."""

    run_id: str
    status: str = "queued"
    started_at: str = field(default_factory=lambda: _timestamp())
    finished_at: str | None = None
    total: int = 0
    current_test_id: str | None = None
    outcomes: list[TestOutcome] = field(default_factory=list)
    error: str | None = None
    cancel_requested: bool = False


class RunManager:
    """Run one configuration at a time without blocking the API event loop."""

    def __init__(
        self,
        config_path: str | Path,
        results_directory: str | Path,
        *,
        runner: ValidationRunner | None = None,
    ) -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        self.runner = runner or ValidationRunner(results_directory=results_directory)
        self.broker = EventBroker()
        self._records: dict[str, RunRecord] = {}
        self._lock = threading.RLock()
        self._cancel_events: dict[str, threading.Event] = {}

    async def start(self) -> dict[str, Any]:
        """Start a background run and return its initial state."""
        loop = asyncio.get_running_loop()
        self.broker.bind(loop)
        with self._lock:
            if any(record.status in {"queued", "running"} for record in self._records.values()):
                raise RuntimeError("A validation run is already active")
            run_id = uuid.uuid4().hex
            record = RunRecord(run_id)
            self._records[run_id] = record
            self._cancel_events[run_id] = threading.Event()
        asyncio.create_task(asyncio.to_thread(self._run, run_id))
        return self.snapshot(run_id)

    def cancel(self, run_id: str) -> dict[str, Any]:
        """Request cancellation after the active command completes."""
        with self._lock:
            record = self._record(run_id)
            if record.status not in {"queued", "running"}:
                return self._snapshot(record)
            record.cancel_requested = True
            self._cancel_events[run_id].set()
        self.broker.publish_from_thread(
            run_id,
            {"type": "log", "level": "warning", "message": "Cancellation requested"},
        )
        return self.snapshot(run_id)

    def snapshot(self, run_id: str) -> dict[str, Any]:
        """Return a JSON-safe state snapshot."""
        with self._lock:
            return self._snapshot(self._record(run_id))

    def latest(self) -> dict[str, Any] | None:
        """Return the newest run, if any."""
        with self._lock:
            if not self._records:
                return None
            latest_id = next(reversed(self._records))
            return self._snapshot(self._records[latest_id])

    def _run(self, run_id: str) -> None:
        record = self._record(run_id)
        cancel_event = self._cancel_events[run_id]
        try:
            with self._lock:
                record.status = "running"
            self._publish(run_id, "run_started", message="Starting discovery")
            config = self.runner.loader.load(self.config_path)
            inventory = self.runner.discover(config)
            cases = self.runner.expander.expand(config, inventory)
            for case in cases:
                for definition in case.validations:
                    self.runner.validators.create(definition)
            with self._lock:
                record.total = len(cases)
            self._publish(
                run_id,
                "inventory",
                inventory=inventory.to_dict(),
                total=len(cases),
            )

            for case in cases:
                if cancel_event.is_set():
                    break
                with self._lock:
                    record.current_test_id = case.test_id
                self._publish(run_id, "test_started", test=_case_payload(case))
                outcome = self.runner.run_case(case, inventory)
                with self._lock:
                    record.outcomes.append(outcome)
                result = outcome.command_result
                if result and result.stdout:
                    self._publish(
                        run_id,
                        "log",
                        level="stdout",
                        test_id=case.test_id,
                        message=result.stdout,
                    )
                if result and result.stderr:
                    self._publish(
                        run_id,
                        "log",
                        level="stderr",
                        test_id=case.test_id,
                        message=result.stderr,
                    )
                self._publish(run_id, "test_completed", outcome=_outcome_payload(outcome))

            finished = _timestamp()
            summary = RunSummary(
                outcomes=list(record.outcomes),
                inventory=inventory,
                started_at=record.started_at,
                finished_at=finished,
            )
            self.runner.reporter.generate(
                summary, self.runner.results_directory / "report.html"
            )
            with self._lock:
                record.current_test_id = None
                record.finished_at = finished
                record.status = "cancelled" if cancel_event.is_set() else "completed"
            self._publish(run_id, "run_completed", summary=self._summary_payload(record))
        except Exception as exc:  # API boundary must retain failure details
            with self._lock:
                record.current_test_id = None
                record.finished_at = _timestamp()
                record.status = "failed"
                record.error = str(exc)
            self._publish(run_id, "run_failed", message=str(exc))

    def _publish(self, run_id: str, event_type: str, **values: Any) -> None:
        self.broker.publish_from_thread(run_id, {"type": event_type, **values})

    def _record(self, run_id: str) -> RunRecord:
        try:
            return self._records[run_id]
        except KeyError as exc:
            raise KeyError(f"Unknown run: {run_id}") from exc

    def _snapshot(self, record: RunRecord) -> dict[str, Any]:
        return {
            "run_id": record.run_id,
            "status": record.status,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "total": record.total,
            "current_test_id": record.current_test_id,
            "cancel_requested": record.cancel_requested,
            "error": record.error,
            "summary": self._summary_payload(record),
            "outcomes": [_outcome_payload(outcome) for outcome in record.outcomes],
        }

    @staticmethod
    def _summary_payload(record: RunRecord) -> dict[str, int | float]:
        passed = sum(outcome.status == "passed" for outcome in record.outcomes)
        failed = sum(outcome.status == "failed" for outcome in record.outcomes)
        skipped = sum(outcome.status == "skipped" for outcome in record.outcomes)
        executed = passed + failed
        return {
            "total": record.total,
            "completed": len(record.outcomes),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "pass_percentage": (passed / executed * 100.0) if executed else 0.0,
        }


def _case_payload(case: TestCase) -> dict[str, Any]:
    return {
        "test_id": case.test_id,
        "name": case.name,
        "command": case.command,
        "timeout": case.timeout,
        "skip_reason": case.skip_reason,
    }


def _outcome_payload(outcome: TestOutcome) -> dict[str, Any]:
    result = outcome.command_result
    return {
        **_case_payload(outcome.case),
        "status": outcome.status,
        "stdout": result.stdout if result else "",
        "stderr": result.stderr if result else "",
        "exit_code": result.exit_code if result else None,
        "execution_time": result.execution_time if result else None,
        "validations": [
            {
                "passed": validation.passed,
                "message": validation.message,
                "validator": validation.validator,
                "details": validation.details,
            }
            for validation in outcome.validations
        ],
    }


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()




