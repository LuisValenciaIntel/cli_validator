"""FastAPI application for the CLI Validator web console."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cli_validator.config import ConfigurationError
from cli_validator.models import Inventory, TestCase
from cli_validator.runner import ValidationRunner
from cli_validator.web.manager import RunManager


class ConfigDocument(BaseModel):
    """YAML content supplied by the web editor."""

    content: str = Field(min_length=1, max_length=1_000_000)


def create_app(
    *,
    config_path: str | Path = "config/commands.yml",
    results_directory: str | Path = "results",
    frontend_directory: str | Path | None = None,
    manager: RunManager | None = None,
) -> FastAPI:
    """Create an independently configurable FastAPI application."""
    resolved_config = Path(config_path).expanduser().resolve()
    resolved_results = Path(results_directory).expanduser().resolve()
    run_manager = manager or RunManager(resolved_config, resolved_results)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        run_manager.broker.bind(__import__("asyncio").get_running_loop())
        yield

    app = FastAPI(
        title="CLI Validator API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.config_path = resolved_config
    app.state.run_manager = run_manager
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "PUT", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        if not resolved_config.is_file():
            raise HTTPException(404, f"Configuration not found: {resolved_config}")
        content = resolved_config.read_text(encoding="utf-8")
        return {
            "path": str(resolved_config),
            "content": content,
            "document": _parse_document(content),
            "modified_at": resolved_config.stat().st_mtime,
        }

    @app.post("/api/config/validate")
    def validate_config(document: ConfigDocument) -> dict[str, Any]:
        return _validate_document(document.content, resolved_config)

    @app.put("/api/config")
    def save_config(document: ConfigDocument) -> dict[str, Any]:
        latest = run_manager.latest()
        if latest and latest["status"] in {"queued", "running"}:
            raise HTTPException(409, "Configuration cannot be changed during an active run")
        validated = _validate_document(document.content, resolved_config)
        resolved_config.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{resolved_config.name}.", suffix=".tmp", dir=resolved_config.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as temporary:
                temporary.write(document.content)
                if not document.content.endswith("\n"):
                    temporary.write("\n")
            os.replace(temporary_name, resolved_config)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        return {"saved": True, **validated}

    @app.post("/api/runs", status_code=202)
    async def start_run() -> dict[str, Any]:
        if not resolved_config.is_file():
            raise HTTPException(404, f"Configuration not found: {resolved_config}")
        try:
            return await run_manager.start()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/runs/latest")
    def latest_run() -> dict[str, Any] | None:
        return run_manager.latest()

    @app.get("/api/runs/{run_id}")
    def run_status(run_id: str) -> dict[str, Any]:
        try:
            return run_manager.snapshot(run_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/runs/{run_id}/cancel", status_code=202)
    def cancel_run(run_id: str) -> dict[str, Any]:
        try:
            return run_manager.cancel(run_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.websocket("/api/runs/{run_id}/events")
    async def run_events(websocket: WebSocket, run_id: str) -> None:
        try:
            snapshot = run_manager.snapshot(run_id)
        except KeyError:
            await websocket.close(code=4404, reason="Unknown run")
            return
        await websocket.accept()
        queue = run_manager.broker.subscribe(run_id)
        await websocket.send_json({"type": "snapshot", **snapshot})
        try:
            if snapshot["status"] not in {"queued", "running"}:
                await websocket.close(code=1000)
                return
            while True:
                event = await queue.get()
                await websocket.send_json(event)
                if event["type"] in {"run_completed", "run_failed"}:
                    await websocket.close(code=1000)
                    return
        except WebSocketDisconnect:
            pass
        finally:
            run_manager.broker.unsubscribe(run_id, queue)

    report = resolved_results / "report.html"

    @app.get("/api/report")
    def get_report() -> FileResponse:
        if not report.is_file():
            raise HTTPException(404, "No report has been generated yet")
        return FileResponse(report, media_type="text/html")

    frontend = _frontend_path(frontend_directory)
    if frontend.is_dir() and (frontend / "index.html").is_file():
        assets = frontend / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend_route(path: str) -> FileResponse:
            candidate = (frontend / path).resolve()
            if candidate.is_relative_to(frontend) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(frontend / "index.html")

    return app


def _validate_document(content: str, config_path: Path) -> dict[str, Any]:
    parsed = _parse_document(content)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{config_path.name}.validate.", suffix=".yml", dir=config_path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            temporary.write(content)
        runner = ValidationRunner()
        cases = runner.validate_configuration(temporary_name, Inventory())
    except (ConfigurationError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return {
        "valid": True,
        "document": parsed,
        "tests": [_test_payload(case) for case in cases],
    }


def _parse_document(content: str) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(422, f"Invalid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(422, "Configuration root must be a mapping")
    return parsed


def _test_payload(case: TestCase) -> dict[str, Any]:
    return {
        "test_id": case.test_id,
        "name": case.name,
        "command": case.command,
        "timeout": case.timeout,
        "skip_reason": case.skip_reason,
        "validations": case.validations,
    }


def _frontend_path(configured: str | Path | None) -> Path:
    if configured is not None:
        return Path(configured).expanduser().resolve()
    return Path(__file__).parent / "static"




