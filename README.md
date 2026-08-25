# CLI Validator

Production-oriented Python 3.10+ framework for discovering hardware, expanding dynamic command tests, executing Linux CLI applications, validating their output, and producing auditable evidence and HTML reports.

## Features

- Captures stdout, stderr, exit code, wall-clock time, attempts and timeouts.
- Retries failed commands with a configurable delay.
- Discovers PCIe BDFs and link generations dynamically from `lspci -D -vv`.
- Merges JSON inventories from commands such as `ipss probehardware --format json`.
- Expands `foreach` tests from inventory lists without hardcoded identifiers.
- Resolves Jinja variables from inventory, user variables and `env`.
- Skips tests using platform `capabilities`, avoiding per-platform YAML files.
- Includes contains, not-contains, regex, exit-code, JSON-path, table and file validators.
- Stores one evidence directory per test and generates a self-contained Jinja2 HTML report.
- Exposes generated cases as pytest parameters.
- Provides abstract discovery, validator and report interfaces for plugins.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`; on `cmd.exe`, use `.venv\Scripts\activate.bat`.

## Quick start

```bash
cli-validator discover --config config/commands.yml
cli-validator validate config/commands.yml
cli-validator run config/commands.yml
cli-validator report --results results
```

From a source checkout, `python main.py ...` supports the same commands. `run` returns exit code `1` when any test fails; malformed configuration returns `2`. This makes the command directly usable as a GitHub Actions step.

## React web console

The optional web console provides a visual test catalog, editable YAML, configuration
validation, live execution progress, WebSocket logs, per-test stdout/stderr and report access.
The compiled React application is served by the same FastAPI process as the API.

```bash
python -m pip install -e ".[dev]"
cd frontend
npm install
npm run build
cd ..
cli-validator serve --config config/commands.yml --results results
```

Open `http://127.0.0.1:8000`. The server binds to localhost by default. To expose it on a
trusted validation network, pass `--host 0.0.0.0`; add authentication and TLS at a reverse
proxy before exposing it outside that network because saved YAML commands execute on the host.

The UI supports:

- Visual and raw-YAML views of `commands.yml`, including expanded test previews.
- Server-side validation before saving and atomic file replacement after validation succeeds.
- `Ctrl+S`/`Cmd+S`, Validate, Save and Save-and-Run workflows.
- One active run at a time, preventing overlapping evidence writes.
- WebSocket events for run/test lifecycle, stdout, stderr, inventory and summaries.
- Cooperative cancellation after the currently executing command completes or times out.
- Per-test validation details and the generated HTML evidence report.

For frontend development, run the API and Vite dev server in separate terminals. Vite proxies
HTTP and WebSocket `/api` requests to port 8000:

```bash
cli-validator serve --config config/commands.yml
cd frontend
npm run dev
```

The frontend requires Node.js `20.12+` and npm `10+`. If Vite reports
`crypto.getRandomValues is not a function`, verify `node --version` in the same terminal and
restore the locked local toolchain instead of using a global Vite installation:

```bash
cd frontend
npm ci
npm run dev
```

The API documentation is available at `http://127.0.0.1:8000/docs`. There is intentionally no
endpoint for arbitrary ad-hoc commands: the web console can only execute tests saved in the
configured YAML file.

## YAML format

```yaml
discovery:
  - type: pcie
  - type: json_command
    command: ipss probehardware --format json

variables:
  timeout: 120
  expected_family: "{{ env.get('EXPECTED_FAMILY', 'PCIe') }}"

defaults:
  timeout: "{{ timeout }}"
  retry: {count: 3, delay: 10}

tests:
  - name: Show {{ item }}
    foreach: pcie_devices
    command: ipss showdevice {{ item }}
    validations:
      - {type: contains, value: "{{ expected_family }}"}
      - {type: not_contains, value: ERROR, source: combined}
      - {type: exit_code, value: 0}

  - name: Gen6 JSON state
    foreach: gen6_devices
    requires: [pcie_gen6]
    command: ipss showdevice {{ item }} --json
    validations:
      - type: json
        path: items[0].status.phase
        value: Running
```

Template context contains:

- All top-level inventory values (`pcie_devices`, `gen6_devices`, etc.).
- The complete inventory under `inventory`.
- Capabilities under `capabilities`.
- Environment variables under `env`, for example `{{ env.HOME }}` or `{{ env.get('NAME', 'default') }}`.
- User-defined values from `variables`.
- `item` inside a `foreach` test.

`requires` accepts a capability name, a list (all must be true), or expected values:

```yaml
requires:
  pcie_gen6: true
  target_mode: false
```

A test can also use a Jinja condition such as `when: capabilities.iomt and platform == 'OKS'`. Empty discovery lists generate a visible skipped test instead of silently removing coverage.

`stderr` is allowed by default because some commands emit non-fatal warnings there. Require an
empty `stderr` only for tests where it is meaningful:

```yaml
tests:
  - name: Warnings are allowed
    command: ipss command-that-warns

  - name: stderr must be empty
    command: ipss strict-command
    check_stderr: true
```

`check_stderr` must be `true` or `false` and can also be placed under `defaults`. The equivalent
explicit validator is `{type: empty, source: stderr}`.

## Validators

All validators accept `type`; text validators additionally accept `source: stdout|stderr|combined`.

- `contains`, `not_contains`: `value`, optional `case_sensitive`; `contains` also accepts
  `normalize: true` to ignore punctuation and whitespace in legacy CLI output.
- `regex`: `value`, optional `ignore_case`.
- `empty`: requires the selected `source` to contain zero characters.
- `exit_code`: integer `value`.
- `json`: `path` and typed `value`. Paths support `items[0].status.phase` and `["key.with.dots"]`.
- `table`: `column` and `mode: all|any|count|min_count|values`. The first three compare
  `value`; `count` requires an exact matching-row `count`, `min_count` requires at least
  `count` data rows, and `values` compares the complete column against a `values` list.
- `file_exists`: path in `value`, relative to the command working directory.

Tables may use Unicode box-drawing characters, pipes, or two or more spaces between columns.

## Evidence and reports

Each run writes:

```text
results/
├── test_001/
│   ├── command.txt
│   ├── stdout.txt
│   ├── stderr.txt
│   ├── metadata.json
│   └── validation.json
└── report.html
```

Metadata includes hostname, discovered platform, OS, user, UTC timestamp, command, execution time, exit code, timeout state and attempt count. `cli-validator report` can reconstruct HTML from saved evidence without rerunning commands.

## Pytest integration

Installation registers the plugin automatically. Add a normal test consuming the generated outcome:

```python
def test_cli_case(cli_validator_result):
    assert cli_validator_result.status in {"passed", "skipped"}
```

Run it with:

```bash
pytest --cli-validator-config config/commands.yml --cli-validator-results results/pytest
```

## Extending the framework

Implement one of the stable abstract interfaces:

```python
from cli_validator.discovery import DiscoveryProvider

class UsbDiscovery(DiscoveryProvider):
    def discover(self) -> dict:
        return {"usb_devices": [], "capabilities": {"usb": True}}
```

Custom validators subclass `BaseValidator`, implement `validate(result)`, and register with `ValidatorRegistry.register`. Custom reports subclass `BaseReportGenerator`. Dependencies can be supplied to `ValidationRunner`, so integrations for Jira, TestRail, Zephyr, snapshots, baselines or trend storage can remain separate from execution logic.

## CI example

```yaml
- uses: actions/setup-python@v5
  with:
    python-version: "3.12"
- run: pip install .
- run: cli-validator run config/commands.yml --results results
- uses: actions/upload-artifact@v4
  if: always()
  with:
    name: cli-validator-results
    path: results/
```

## Development

```bash
python -m pytest
ruff check .
mypy cli_validator
```





