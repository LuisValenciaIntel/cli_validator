import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  eventUrl,
  type ConfigDocument,
  type RunEvent,
  type RunState,
  type TestDefinition,
  type TestOutcome,
} from "./api";

type View = "visual" | "yaml";

type LogEntry = {
  timestamp: string;
  level: string;
  message: string;
  testId?: string;
};

const emptySummary = {
  total: 0,
  completed: 0,
  passed: 0,
  failed: 0,
  skipped: 0,
  pass_percentage: 0,
};

export default function App() {
  const [content, setContent] = useState("");
  const [document, setDocument] = useState<ConfigDocument>({});
  const [expandedTests, setExpandedTests] = useState<TestDefinition[]>([]);
  const [configPath, setConfigPath] = useState("");
  const [view, setView] = useState<View>("visual");
  const [dirty, setDirty] = useState(false);
  const [notice, setNotice] = useState("Loading configuration…");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState<RunState | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [selected, setSelected] = useState<TestOutcome | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const logEndRef = useRef<HTMLDivElement | null>(null);

  const connect = useCallback((runId: string) => {
    socketRef.current?.close();
    const socket = new WebSocket(eventUrl(runId));
    socketRef.current = socket;
    socket.onopen = () =>
      setLogs((items) => [
        ...items,
        { timestamp: new Date().toISOString(), level: "system", message: "Live stream connected" },
      ]);
    socket.onmessage = ({ data }) => {
      const event = JSON.parse(String(data)) as RunEvent;
      if (event.type === "snapshot") {
        setRun(event as RunState & RunEvent);
        return;
      }
      if (event.type === "run_started") {
        setRun((current) => current && { ...current, status: "running" });
      } else if (event.type === "inventory") {
        setRun((current) =>
          current
            ? { ...current, total: event.total ?? current.total, summary: { ...current.summary, total: event.total ?? current.total } }
            : current,
        );
      } else if (event.type === "test_started") {
        setRun((current) =>
          current ? { ...current, current_test_id: event.test?.test_id ?? null } : current,
        );
        if (event.test) {
          appendLog("command", `$ ${event.test.command}`, event.test.test_id);
        }
      } else if (event.type === "log" && event.message) {
        appendLog(event.level ?? "info", event.message, event.test_id);
      } else if (event.type === "test_completed" && event.outcome) {
        setRun((current) => {
          if (!current) return current;
          const outcomes = [
            ...current.outcomes.filter((item) => item.test_id !== event.outcome?.test_id),
            event.outcome as TestOutcome,
          ];
          const passed = outcomes.filter((item) => item.status === "passed").length;
          const failed = outcomes.filter((item) => item.status === "failed").length;
          const skipped = outcomes.filter((item) => item.status === "skipped").length;
          return {
            ...current,
            outcomes,
            summary: {
              ...current.summary,
              completed: outcomes.length,
              passed,
              failed,
              skipped,
              pass_percentage: passed + failed ? (passed / (passed + failed)) * 100 : 0,
            },
          };
        });
      } else if (event.type === "run_completed" && event.summary) {
        setRun((current) =>
          current
            ? {
                ...current,
                status: current.cancel_requested ? "cancelled" : "completed",
                current_test_id: null,
                summary: event.summary as RunState["summary"],
              }
            : current,
        );
        appendLog("system", "Run completed");
      } else if (event.type === "run_failed") {
        setRun((current) =>
          current ? { ...current, status: "failed", error: event.message ?? "Run failed" } : current,
        );
        appendLog("error", event.message ?? "Run failed");
      }
    };
    socket.onerror = () => appendLog("error", "Live stream connection error");
  }, []);

  const appendLog = (level: string, message: string, testId?: string) => {
    setLogs((items) => [
      ...items.slice(-999),
      { timestamp: new Date().toISOString(), level, message, testId },
    ]);
  };

  const load = useCallback(async () => {
    try {
      const [config, latest] = await Promise.all([api.getConfig(), api.latestRun()]);
      setContent(config.content);
      setDocument(config.document ?? {});
      setConfigPath(config.path);
      setRun(latest);
      setDirty(false);
      setNotice("Configuration loaded");
      if (latest?.status === "queued" || latest?.status === "running") connect(latest.run_id);
    } catch (reason) {
      setError(messageOf(reason));
      setNotice("");
    }
  }, [connect]);

  useEffect(() => {
    void load();
    return () => socketRef.current?.close();
  }, [load]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const validate = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await api.validateConfig(content);
      setDocument(response.document ?? {});
      setExpandedTests(response.tests);
      setNotice(`Valid configuration · ${response.tests.length} expanded tests`);
      setView("visual");
    } catch (reason) {
      setError(messageOf(reason));
      setNotice("");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await api.saveConfig(content);
      setDocument(response.document ?? {});
      setExpandedTests(response.tests);
      setDirty(false);
      setNotice("Configuration saved atomically");
      return true;
    } catch (reason) {
      setError(messageOf(reason));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const startRun = async () => {
    if (dirty && !(await save())) return;
    setBusy(true);
    setError("");
    setLogs([]);
    setSelected(null);
    try {
      const created = await api.startRun();
      setRun({ ...created, summary: created.summary ?? emptySummary });
      setNotice("Validation run started");
      connect(created.run_id);
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!run) return;
    try {
      setRun(await api.cancelRun(run.run_id));
    } catch (reason) {
      setError(messageOf(reason));
    }
  };

  const active = run?.status === "queued" || run?.status === "running";
  const tests = expandedTests.length ? expandedTests : document?.tests ?? [];
  const progress = run?.summary.total
    ? Math.round((run.summary.completed / run.summary.total) * 100)
    : 0;
  const validationsCount = useMemo(
    () => tests.reduce((total, test) => total + (test.validations?.length ?? 0), 0),
    [tests],
  );

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">CV</div>
          <div>
            <h1>CLI Validator</h1>
            <p>Hardware validation command center</p>
          </div>
        </div>
        <div className="header-actions">
          <span className={`connection ${active ? "live" : ""}`}>
            <i /> {active ? "Execution live" : "Ready"}
          </span>
          <button className="button ghost" onClick={() => void load()} disabled={busy || active}>
            Reload
          </button>
          {active ? (
            <button className="button danger" onClick={() => void cancel()}>
              Stop after command
            </button>
          ) : (
            <button className="button primary" onClick={() => void startRun()} disabled={busy}>
              Run validation
            </button>
          )}
        </div>
      </header>

      <main>
        {(notice || error) && (
          <div className={`notice ${error ? "error" : ""}`}>
            <span>{error || notice}</span>
            {error && <button onClick={() => setError("")}>×</button>}
          </div>
        )}

        <section className="metrics">
          <Metric label="Configured tests" value={tests.length} accent="cyan" />
          <Metric label="Validations" value={validationsCount} accent="violet" />
          <Metric label="Passed" value={run?.summary.passed ?? 0} accent="green" />
          <Metric label="Failed" value={run?.summary.failed ?? 0} accent="red" />
          <Metric label="Pass rate" value={`${(run?.summary.pass_percentage ?? 0).toFixed(0)}%`} accent="amber" />
        </section>

        <section className="workspace-grid">
          <div className="panel config-panel">
            <div className="panel-header">
              <div>
                <span className="eyebrow">Configuration</span>
                <h2>commands.yml {dirty && <b className="dirty">●</b>}</h2>
                <p className="path" title={configPath}>{configPath}</p>
              </div>
              <div className="tabs">
                <button className={view === "visual" ? "active" : ""} onClick={() => setView("visual")}>Visual</button>
                <button className={view === "yaml" ? "active" : ""} onClick={() => setView("yaml")}>YAML</button>
              </div>
            </div>

            {view === "yaml" ? (
              <textarea
                className="yaml-editor"
                value={content}
                spellCheck={false}
                aria-label="commands.yml editor"
                onChange={(event) => {
                  setContent(event.target.value);
                  setDirty(true);
                }}
                onKeyDown={(event) => {
                  if ((event.ctrlKey || event.metaKey) && event.key === "s") {
                    event.preventDefault();
                    void save();
                  }
                }}
              />
            ) : (
              <div className="test-list">
                {tests.map((test, index) => (
                  <article className="test-card" key={`${test.test_id ?? index}-${test.command}`}>
                    <div className="test-index">{String(index + 1).padStart(2, "0")}</div>
                    <div className="test-body">
                      <div className="test-title-row">
                        <h3>{test.name || `Test ${index + 1}`}</h3>
                        {test.foreach && <span className="tag">foreach · {test.foreach}</span>}
                        {test.skip_reason && <span className="tag skip">skipped</span>}
                      </div>
                      <code>$ {test.command}</code>
                      <div className="validator-tags">
                        {(test.validations ?? []).map((validation, validationIndex) => (
                          <span key={`${validation.type}-${validationIndex}`}>{validation.type}</span>
                        ))}
                      </div>
                    </div>
                  </article>
                ))}
                {!tests.length && <div className="empty">Validate the YAML to preview expanded tests.</div>}
              </div>
            )}

            <div className="panel-footer">
              <span>{dirty ? "Unsaved changes" : "Saved"}</span>
              <div>
                <button className="button ghost" onClick={() => void validate()} disabled={busy}>Validate</button>
                <button className="button secondary" onClick={() => void save()} disabled={busy || active}>Save</button>
              </div>
            </div>
          </div>

          <div className="right-column">
            <section className="panel execution-panel">
              <div className="panel-header compact">
                <div>
                  <span className="eyebrow">Execution</span>
                  <h2>{run ? `Run ${run.run_id.slice(0, 8)}` : "No active run"}</h2>
                </div>
                <span className={`status-pill ${run?.status ?? "idle"}`}>{run?.status ?? "idle"}</span>
              </div>
              <div className="progress-track"><div style={{ width: `${progress}%` }} /></div>
              <div className="progress-labels">
                <span>{run?.summary.completed ?? 0} / {run?.summary.total ?? 0} tests</span>
                <strong>{progress}%</strong>
              </div>
              <div className="outcome-list">
                {run?.outcomes.map((outcome) => (
                  <button key={outcome.test_id} onClick={() => setSelected(outcome)}>
                    <span className={`outcome-dot ${outcome.status}`} />
                    <span>{outcome.name}</span>
                    <small>{outcome.execution_time?.toFixed(2) ?? "–"}s</small>
                  </button>
                ))}
                {!run?.outcomes.length && <div className="empty small">Results appear here as commands complete.</div>}
              </div>
              {run?.outcomes.length ? <a className="report-link" href="/api/report" target="_blank" rel="noreferrer">Open HTML evidence report ↗</a> : null}
            </section>

            <section className="panel console-panel">
              <div className="console-header">
                <div><i /> Live execution log</div>
                <button onClick={() => setLogs([])}>Clear</button>
              </div>
              <div className="console" role="log" aria-live="polite">
                {logs.map((entry, index) => (
                  <div className={`log-line ${entry.level}`} key={`${entry.timestamp}-${index}`}>
                    <time>{new Date(entry.timestamp).toLocaleTimeString()}</time>
                    <span className="log-level">{entry.level}</span>
                    <pre>{entry.message}</pre>
                  </div>
                ))}
                {!logs.length && <div className="console-empty">Waiting for a validation run…</div>}
                <div ref={logEndRef} />
              </div>
            </section>
          </div>
        </section>
      </main>

      {selected && (
        <div className="modal-backdrop" onMouseDown={() => setSelected(null)}>
          <div className="modal" onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-header">
              <div><span className={`status-pill ${selected.status}`}>{selected.status}</span><h2>{selected.name}</h2></div>
              <button onClick={() => setSelected(null)}>×</button>
            </div>
            <code className="modal-command">$ {selected.command}</code>
            <div className="modal-meta"><span>Exit {selected.exit_code ?? "–"}</span><span>{selected.execution_time?.toFixed(3) ?? "–"} seconds</span></div>
            <h3>Validations</h3>
            <ul className="validation-list">
              {selected.validations.map((validation, index) => (
                <li className={validation.passed ? "passed" : "failed"} key={index}>
                  <b>{validation.passed ? "✓" : "×"}</b><span>{validation.message}</span>
                </li>
              ))}
            </ul>
            <h3>Standard output</h3><pre className="output-block">{selected.stdout || "(empty)"}</pre>
            <h3>Standard error</h3><pre className="output-block error-output">{selected.stderr || "(empty)"}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, accent }: { label: string; value: string | number; accent: string }) {
  return <div className={`metric ${accent}`}><span>{label}</span><strong>{value}</strong></div>;
}

function messageOf(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}


