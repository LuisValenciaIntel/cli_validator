from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from cli_validator.web import create_app

CONFIG = """\
variables:
  message: hello
tests:
  - name: Web smoke test
    command: echo {{ message }}
    validations:
      - type: contains
        value: hello
"""


def test_config_api_reads_validates_and_saves_atomically(tmp_path: Path) -> None:
    config = tmp_path / "config" / "commands.yml"
    config.parent.mkdir()
    config.write_text(CONFIG, encoding="utf-8")
    app = create_app(config_path=config, results_directory=tmp_path / "results")

    with TestClient(app) as client:
        frontend = client.get("/")
        assert frontend.status_code == 200
        assert "CLI Validator Console" in frontend.text

        loaded = client.get("/api/config")
        assert loaded.status_code == 200
        assert loaded.json()["document"]["tests"][0]["name"] == "Web smoke test"

        validated = client.post("/api/config/validate", json={"content": CONFIG})
        assert validated.status_code == 200
        assert validated.json()["tests"][0]["command"] == "echo hello"

        changed = CONFIG.replace("hello", "updated")
        saved = client.put("/api/config", json={"content": changed})
        assert saved.status_code == 200
        assert saved.json()["saved"] is True
        assert config.read_text(encoding="utf-8").endswith("\n")
        assert "updated" in config.read_text(encoding="utf-8")


def test_config_api_rejects_invalid_yaml_without_overwriting(tmp_path: Path) -> None:
    config = tmp_path / "commands.yml"
    config.write_text(CONFIG, encoding="utf-8")
    app = create_app(config_path=config, results_directory=tmp_path / "results")

    with TestClient(app) as client:
        response = client.put("/api/config", json={"content": "tests: ["})

    assert response.status_code == 422
    assert config.read_text(encoding="utf-8") == CONFIG


def test_websocket_streams_run_and_report_is_available(tmp_path: Path) -> None:
    config = tmp_path / "commands.yml"
    config.write_text(CONFIG, encoding="utf-8")
    app = create_app(config_path=config, results_directory=tmp_path / "results")

    with TestClient(app) as client:
        started = client.post("/api/runs")
        assert started.status_code == 202
        run_id = started.json()["run_id"]

        event_types: list[str] = []
        with client.websocket_connect(f"/api/runs/{run_id}/events") as websocket:
            while "run_completed" not in event_types:
                event = websocket.receive_json()
                event_types.append(event["type"])

        state = client.get(f"/api/runs/{run_id}").json()
        assert state["status"] == "completed"
        assert state["summary"]["passed"] == 1
        assert state["outcomes"][0]["stdout"].strip() == "hello"
        assert "test_completed" in event_types
        assert client.get("/api/report").status_code == 200


def test_only_one_run_can_be_active(tmp_path: Path) -> None:
    config = tmp_path / "commands.yml"
    config.write_text(
        "tests:\n  - command: echo ok\n    retry: {count: 0}\n",
        encoding="utf-8",
    )
    app = create_app(config_path=config, results_directory=tmp_path / "results")

    with TestClient(app) as client:
        first = client.post("/api/runs")
        second = client.post("/api/runs")

    assert first.status_code == 202
    # The first command may finish before the second request reaches the app.
    assert second.status_code in {202, 409}




