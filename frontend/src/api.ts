export type ValidationDefinition = {
  type: string;
  [key: string]: unknown;
};

export type TestDefinition = {
  test_id?: string;
  name?: string;
  command: string;
  timeout?: number;
  foreach?: string;
  requires?: string | string[] | Record<string, unknown>;
  skip_reason?: string | null;
  validations?: ValidationDefinition[];
};

export type ConfigDocument = {
  discovery?: Array<Record<string, unknown>>;
  variables?: Record<string, unknown>;
  defaults?: Record<string, unknown>;
  tests?: TestDefinition[];
};

export type ConfigResponse = {
  path: string;
  content: string;
  document: ConfigDocument;
  modified_at: number;
};

export type ValidationResponse = {
  valid: true;
  document: ConfigDocument;
  tests: TestDefinition[];
  saved?: boolean;
};

export type ValidationOutcome = {
  passed: boolean;
  message: string;
  validator: string;
};

export type TestOutcome = Omit<TestDefinition, "validations"> & {
  test_id: string;
  name: string;
  status: "passed" | "failed" | "skipped";
  stdout: string;
  stderr: string;
  exit_code: number | null;
  execution_time: number | null;
  validations: ValidationOutcome[];
};

export type RunSummary = {
  total: number;
  completed: number;
  passed: number;
  failed: number;
  skipped: number;
  pass_percentage: number;
};

export type RunState = {
  run_id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  started_at: string;
  finished_at: string | null;
  total: number;
  current_test_id: string | null;
  cancel_requested: boolean;
  error: string | null;
  summary: RunSummary;
  outcomes: TestOutcome[];
};

export type RunEvent = {
  type: string;
  run_id?: string;
  timestamp?: string;
  message?: string;
  level?: string;
  test_id?: string;
  test?: TestDefinition;
  outcome?: TestOutcome;
  summary?: RunSummary;
  inventory?: Record<string, unknown>;
} & Partial<RunState>;

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({ detail: response.statusText }));
  if (!response.ok) {
    throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
  }
  return body as T;
}

export const api = {
  getConfig: () => request<ConfigResponse>("/api/config"),
  validateConfig: (content: string) =>
    request<ValidationResponse>("/api/config/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }),
  saveConfig: (content: string) =>
    request<ValidationResponse>("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }),
  startRun: () => request<RunState>("/api/runs", { method: "POST" }),
  latestRun: () => request<RunState | null>("/api/runs/latest"),
  cancelRun: (runId: string) =>
    request<RunState>(`/api/runs/${runId}/cancel`, { method: "POST" }),
};

export function eventUrl(runId: string): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/runs/${runId}/events`;
}


