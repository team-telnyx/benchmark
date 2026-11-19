import { StatefulActor } from "@telnyx/edge-runtime";

type ProviderName =
  | "telnyx"
  | "together"
  | "fireworks"
  | "baseten"
  | "modal_shared"
  | "modal_dedicated";

interface ProviderConfig {
  name: ProviderName;
  baseUrl: string;
  model: string;
  apiKeyEnv: string;
  authScheme?: "Bearer" | "Key";
  maxTokensField?: "max_tokens" | "max_completion_tokens";
  includeStreamOptions?: boolean;
  retryWithoutStreamOptions?: boolean;
  reasoningEffort?: string;
  extraBody?: Record<string, unknown>;
  extraHeaders?: Record<string, string>;
  concurrency?: number;
  cacheMode?: string;
}

interface PromptProfile {
  name: string;
  system: string;
  user: string;
  maxTokens: number;
  inputTarget: string;
  outputTarget: string;
  inputTokens?: number;
  noncePrefix?: string;
  cacheGroup?: string;
  sharedPrefixFraction?: number;
}

interface BenchmarkSuite {
  suite:
    | "deepseek_v4_flash_0731"
    | "deepseek_v4_flash_0731_no_thinking"
    | "deepseek_v4_flash_0731_reasoning_disabled"
    | "qwen38_27b"
    | "qwen38_27b_telnyx_only"
    | "kimi_k3"
    | "minimax_m3"
    | "glm_52"
    | "glm_53"
    | "glm_53_flash";
  providers: ProviderConfig[];
}

interface StartRequest {
  repeats: number;
  profiles: PromptProfile[];
  suites: BenchmarkSuite[];
  phases?: BenchmarkPhase[];
  timeoutMs?: number;
  batchSize?: number;
  autoStart?: boolean;
  tokenUsageProbe?: boolean;
}

interface BenchmarkPhase {
  name: string;
  repeats: number;
  profiles: PromptProfile[];
  suites: BenchmarkSuite[];
  batchSize: number;
  randomSeed?: number;
}

interface PlanCell {
  phase: string;
  suite: string;
  provider: ProviderConfig;
  profile: PromptProfile;
  repeats: number;
  batchSize: number;
}

interface Task {
  suite: string;
  provider: ProviderConfig;
  profile: PromptProfile;
  iteration: number;
}

interface ResultRow {
  suite: string;
  provider: string;
  model: string;
  profile: string;
  iteration: number;
  ok: boolean;
  status: number | null;
  started_at: string;
  e2e_ms: number | null;
  ttft_ms: number | null;
  ttft_content_ms: number | null;
  ttfr_ms: number | null;
  ttfa_ms: number | null;
  first_delta_kind: "reasoning" | "content" | "mixed" | null;
  first_delta_text: string | null;
  first_reasoning_text: string | null;
  first_content_text: string | null;
  full_reasoning_text: string;
  full_content_text: string;
  reasoning_effort_requested: string | null;
  reasoning_effort_status: "sent" | "not_requested";
  concurrency: number | null;
  cache_mode: string | null;
  median_itl_ms: number | null;
  chunks: number;
  output_chars: number;
  reasoning_chars: number;
  prompt_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  effective_tok_s: number | null;
  token_usage_source: "stream" | "nonstream_probe" | null;
  token_usage_error: string | null;
  error: string | null;
}

interface RunState {
  startedAt: string;
  repeats: number;
  timeoutMs: number;
  batchSize: number;
  autoStart: boolean;
  tokenUsageProbe: boolean;
  total: number;
  cursor: number;
  profiles: PromptProfile[];
  suites: BenchmarkSuite[];
  planCells?: PlanCell[];
  planCellIndex?: number;
  planIteration?: number;
  finishedAt?: string;
  lastAlarmAt?: string;
}

interface EnvLike {
  TELNYX_API_KEY?: string;
  TOGETHER_API_KEY?: string;
  FIREWORKS_API_KEY?: string;
  BASETEN_API_KEY?: string;
  MODAL_PROXY_TOKEN?: string;
  [key: string]: unknown;
}

function nowIso(): string {
  return new Date().toISOString();
}

function median(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function compactError(value: unknown): string {
  if (typeof value === "string") return value.slice(0, 800);
  try {
    return JSON.stringify(value).slice(0, 800);
  } catch {
    return String(value).slice(0, 800);
  }
}

function generatedContextChars(targetChars: number, nonce: string): string {
  const chunks: string[] = [
    `Request nonce: ${nonce}. Treat every record below as request-specific context.\n`,
  ];
  let length = chunks[0].length;
  let index = 0;
  while (length < targetChars) {
    const record =
      `Record ${nonce}-${index} describes service ${index % 97}, deployment revision ` +
      `${(index * 37 + nonce.length * 13) % 10007}, observed behavior ${index % 31}, ` +
      `dependency ${index % 53}, and operational constraint ${(index * 17) % 71}. ` +
      "Use the record only as context for the requested synthesis.\n";
    chunks.push(record);
    length += record.length;
    index += 1;
  }
  return chunks.join("").slice(0, targetChars);
}

function generatedContext(targetTokens: number, nonce: string): string {
  return generatedContextChars(Math.max(100, targetTokens * 4), nonce);
}

function cachedContext(task: Task, nonce: string): string {
  const targetChars = Math.max(100, (task.profile.inputTokens ?? 0) * 4);
  const fraction = Math.min(0.98, Math.max(0.5, task.profile.sharedPrefixFraction ?? 0.9));
  const sharedChars = Math.floor(targetChars * fraction);
  const group = task.profile.cacheGroup ?? `${task.profile.name}-${task.provider.concurrency ?? 1}`;
  const shared = generatedContextChars(sharedChars, `shared-cache-${group}`);
  const divider = "\nUNIQUE REQUEST SUFFIX\n";
  const uniqueChars = Math.max(0, targetChars - shared.length - divider.length);
  const unique = generatedContextChars(uniqueChars, nonce);
  return `${shared}${divider}${unique}`.slice(0, targetChars);
}

function messagesForTask(task: Task): Array<{ role: "system" | "user"; content: string }> {
  if (!task.profile.inputTokens) {
    return [
      { role: "system", content: task.profile.system },
      { role: "user", content: task.profile.user },
    ];
  }
  const nonce = `${task.profile.noncePrefix ?? task.profile.name}-${task.iteration}`;
  const usesSharedPrefix = task.provider.cacheMode === "cache_seed" || task.provider.cacheMode === "cached";
  const context = usesSharedPrefix
    ? cachedContext(task, nonce)
    : generatedContext(task.profile.inputTokens, nonce);
  return [
    { role: "system", content: task.profile.system },
    {
      role: "user",
      content:
        `Read the context and produce a structured technical synthesis of approximately ` +
        `${task.profile.maxTokens} tokens. Include a short overview, three findings, and ` +
        `two implementation recommendations.\n\nCONTEXT\n${context}`,
    },
  ];
}

function seededShuffle<T>(values: T[], seed: number): T[] {
  const shuffled = [...values];
  let state = seed >>> 0;
  const random = (): number => {
    state = (Math.imul(1664525, state) + 1013904223) >>> 0;
    return state / 0x100000000;
  };
  for (let index = shuffled.length - 1; index > 0; index -= 1) {
    const swap = Math.floor(random() * (index + 1));
    [shuffled[index], shuffled[swap]] = [shuffled[swap], shuffled[index]];
  }
  return shuffled;
}

function buildPlanCells(phases: BenchmarkPhase[]): PlanCell[] {
  const cells: PlanCell[] = [];
  for (const [phaseIndex, phase] of phases.entries()) {
    const phaseCells: PlanCell[] = [];
    for (const suite of phase.suites) {
      for (const provider of suite.providers) {
        for (const profile of phase.profiles) {
          phaseCells.push({
          phase: phase.name,
          suite: suite.suite,
          provider: {
            ...provider,
            extraBody: provider.extraBody ? { ...provider.extraBody } : undefined,
            extraHeaders: provider.extraHeaders ? { ...provider.extraHeaders } : undefined,
          },
          profile: { ...profile },
            repeats: phase.repeats,
            batchSize: phase.batchSize,
          });
        }
      }
    }
    cells.push(...seededShuffle(phaseCells, phase.randomSeed ?? phaseIndex + 1));
  }
  return cells;
}

function buildTasks(request: StartRequest): Task[] {
  const tasks: Task[] = [];
  for (const suite of request.suites) {
    for (const provider of suite.providers) {
      for (const profile of request.profiles) {
        for (let iteration = 1; iteration <= request.repeats; iteration += 1) {
          tasks.push({ suite: suite.suite, provider, profile, iteration });
        }
      }
    }
  }
  return tasks;
}

function countTasks(request: Pick<StartRequest, "repeats" | "profiles" | "suites">): number {
  return request.suites.reduce(
    (total, suite) => total + suite.providers.length * request.profiles.length * request.repeats,
    0,
  );
}

function taskAtIndex(state: RunState, index: number): Task {
  let offset = index;
  for (const suite of state.suites) {
    for (const provider of suite.providers) {
      for (const profile of state.profiles) {
        for (let iteration = 1; iteration <= state.repeats; iteration += 1) {
          if (offset === 0) {
            return { suite: suite.suite, provider, profile, iteration };
          }
          offset -= 1;
        }
      }
    }
  }
  throw new Error(`Task index out of range: ${index}`);
}

function csvEscape(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  if (/[",\n\r]/.test(text)) return `"${text.replaceAll('"', '""')}"`;
  return text;
}

const RESULT_FIELDS: (keyof ResultRow)[] = [
  "suite",
  "provider",
  "model",
  "profile",
  "iteration",
  "ok",
  "status",
  "started_at",
  "e2e_ms",
  "ttft_ms",
  "ttft_content_ms",
  "ttfr_ms",
  "ttfa_ms",
  "first_delta_kind",
  "first_delta_text",
  "first_reasoning_text",
  "first_content_text",
  "full_reasoning_text",
  "full_content_text",
  "reasoning_effort_requested",
  "reasoning_effort_status",
  "concurrency",
  "cache_mode",
  "median_itl_ms",
  "chunks",
  "output_chars",
  "reasoning_chars",
  "prompt_tokens",
  "output_tokens",
  "total_tokens",
  "effective_tok_s",
  "token_usage_source",
  "token_usage_error",
  "error",
];

async function streamChat(task: Task, env: EnvLike, timeoutMs: number, tokenUsageProbe: boolean): Promise<ResultRow> {
  const apiKey = env[task.provider.apiKeyEnv];
  const startedAt = nowIso();
  if (typeof apiKey !== "string" || !apiKey) {
    return failureRow(task, startedAt, null, `Missing ${task.provider.apiKeyEnv}`);
  }

  const maxTokensField = task.provider.maxTokensField ?? "max_tokens";
  const payload: Record<string, unknown> = {
    model: task.provider.model,
    messages: messagesForTask(task),
    temperature: 0,
    stream: true,
    [maxTokensField]: task.profile.maxTokens,
    ...(task.provider.reasoningEffort
      ? { reasoning_effort: task.provider.reasoningEffort }
      : {}),
    ...(task.provider.extraBody ?? {}),
  };
  if (task.provider.includeStreamOptions) {
    payload.stream_options = {
      include_usage: true,
    };
  }

  return requestWithOptionalRetry(task, env, payload, timeoutMs, startedAt, tokenUsageProbe);
}

async function requestWithOptionalRetry(
  task: Task,
  env: EnvLike,
  payload: Record<string, unknown>,
  timeoutMs: number,
  startedAt: string,
  tokenUsageProbe: boolean,
): Promise<ResultRow> {
  const first = await requestOnce(task, env, payload, timeoutMs, startedAt, tokenUsageProbe);
  if (
    first.ok ||
    !task.provider.retryWithoutStreamOptions ||
    !("stream_options" in payload) ||
    first.status === null
  ) {
    return first;
  }

  const errorText = first.error ?? "";
  if (!/stream_options|include_usage|continuous_usage_stats|unprocessable|invalid/i.test(errorText)) {
    return first;
  }

  const retryPayload = { ...payload };
  delete retryPayload.stream_options;
  const retry = await requestOnce(task, env, retryPayload, timeoutMs, startedAt, tokenUsageProbe);
  if (retry.error) retry.error = `${retry.error} | retried_without_stream_options=true`;
  return retry;
}

async function requestOnce(
  task: Task,
  env: EnvLike,
  payload: Record<string, unknown>,
  timeoutMs: number,
  startedAt: string,
  tokenUsageProbe: boolean,
): Promise<ResultRow> {
  const apiKey = env[task.provider.apiKeyEnv] as string;
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort("timeout"), timeoutMs);
  const start = performance.now();
  let status: number | null = null;
  let firstAny: number | null = null;
  let firstContent: number | null = null;
  let firstReasoning: number | null = null;
  let firstAnswer: number | null = null;
  let firstDeltaKind: "reasoning" | "content" | "mixed" | null = null;
  let firstDeltaText: string | null = null;
  let firstReasoningText: string | null = null;
  let firstContentText: string | null = null;
  let lastChunk: number | null = null;
  const gaps: number[] = [];
  let chunks = 0;
  let outputChars = 0;
  let reasoningChars = 0;
  let fullContentText = "";
  let fullReasoningText = "";
  let usage: Record<string, unknown> = {};

  try {
    const response = await fetch(`${task.provider.baseUrl.replace(/\/$/, "")}/chat/completions`, {
      method: "POST",
      signal: abort.signal,
      headers: {
        Authorization: `${task.provider.authScheme ?? "Bearer"} ${apiKey}`,
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(task.provider.extraHeaders ?? {}),
      },
      body: JSON.stringify(payload),
    });
    status = response.status;
    if (!response.ok) {
      const body = await response.text().catch(() => "");
      return failureRow(task, startedAt, status, body || response.statusText);
    }

    const reader = response.body?.getReader();
    if (!reader) return failureRow(task, startedAt, status, "Response body was not streamable");

    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const parsed = parseSseLine(line);
        if (!parsed) continue;
        if (parsed.usage && typeof parsed.usage === "object") {
          usage = parsed.usage as Record<string, unknown>;
        }
        const emitted = extractEmittedText(parsed);
        if (!emitted.any) continue;
        const now = performance.now();
        chunks += 1;
        if (firstAny === null) {
          firstAny = now;
          firstDeltaKind = emitted.content && emitted.reasoning
            ? "mixed"
            : emitted.content
              ? "content"
              : "reasoning";
          firstDeltaText = emitted.content + emitted.reasoning;
        }
        if (emitted.reasoning && firstReasoning === null) {
          firstReasoning = now;
          firstReasoningText = emitted.reasoning;
        }
        if (emitted.content && firstContent === null) {
          firstContent = now;
          firstContentText = emitted.content;
        }
        if (emitted.content.trim().length > 0 && firstAnswer === null) firstAnswer = now;
        if (lastChunk !== null) gaps.push(now - lastChunk);
        lastChunk = now;
        outputChars += emitted.content.length;
        reasoningChars += emitted.reasoning.length;
        fullContentText += emitted.content;
        fullReasoningText += emitted.reasoning;
      }
    }
  } catch (error) {
    return failureRow(task, startedAt, status, compactError(error));
  } finally {
    clearTimeout(timer);
  }

  const end = performance.now();
  let tokenUsageSource: "stream" | "nonstream_probe" | null = null;
  let tokenUsageError: string | null = null;
  let outputTokens = numberOrNull(usage.completion_tokens);
  let promptTokens = numberOrNull(usage.prompt_tokens);
  let totalTokens = numberOrNull(usage.total_tokens);
  if (outputTokens !== null) {
    tokenUsageSource = "stream";
  } else if (tokenUsageProbe) {
    const probed = await probeUsage(task, env, payload, timeoutMs);
    if (probed.usage) {
      outputTokens = numberOrNull(probed.usage.completion_tokens);
      promptTokens = numberOrNull(probed.usage.prompt_tokens);
      totalTokens = numberOrNull(probed.usage.total_tokens);
      if (outputTokens !== null) tokenUsageSource = "nonstream_probe";
    }
    tokenUsageError = probed.error;
  }
  const effectiveTokS =
    outputTokens && firstAny !== null && end > firstAny ? outputTokens / ((end - firstAny) / 1000) : null;

  return {
    suite: task.suite,
    provider: task.provider.name,
    model: task.provider.model,
    profile: task.profile.name,
    iteration: task.iteration,
    ok: true,
    status,
    started_at: startedAt,
    e2e_ms: end - start,
    ttft_ms: firstAny === null ? null : firstAny - start,
    ttft_content_ms: firstContent === null ? null : firstContent - start,
    ttfr_ms: firstReasoning === null ? null : firstReasoning - start,
    ttfa_ms: firstAnswer === null ? null : firstAnswer - start,
    first_delta_kind: firstDeltaKind,
    first_delta_text: firstDeltaText,
    first_reasoning_text: firstReasoningText,
    first_content_text: firstContentText,
    full_reasoning_text: fullReasoningText,
    full_content_text: fullContentText,
    reasoning_effort_requested: task.provider.reasoningEffort ?? null,
    reasoning_effort_status: task.provider.reasoningEffort ? "sent" : "not_requested",
    concurrency: task.provider.concurrency ?? null,
    cache_mode: task.provider.cacheMode ?? null,
    median_itl_ms: median(gaps),
    chunks,
    output_chars: outputChars,
    reasoning_chars: reasoningChars,
    prompt_tokens: promptTokens,
    output_tokens: outputTokens,
    total_tokens: totalTokens,
    effective_tok_s: effectiveTokS,
    token_usage_source: tokenUsageSource,
    token_usage_error: tokenUsageError,
    error: null,
  };
}

async function probeUsage(
  task: Task,
  env: EnvLike,
  streamPayload: Record<string, unknown>,
  timeoutMs: number,
): Promise<{ usage: Record<string, unknown> | null; error: string | null }> {
  const apiKey = env[task.provider.apiKeyEnv] as string;
  const payload: Record<string, unknown> = { ...streamPayload, stream: false };
  delete payload.stream_options;
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort("timeout"), timeoutMs);
  try {
    const response = await fetch(`${task.provider.baseUrl.replace(/\/$/, "")}/chat/completions`, {
      method: "POST",
      signal: abort.signal,
      headers: {
        Authorization: `${task.provider.authScheme ?? "Bearer"} ${apiKey}`,
        "Content-Type": "application/json",
        ...(task.provider.extraHeaders ?? {}),
      },
      body: JSON.stringify(payload),
    });
    const text = await response.text();
    if (!response.ok) {
      return { usage: null, error: `usage_probe_status=${response.status}: ${text.slice(0, 500)}` };
    }
    const parsed = JSON.parse(text) as Record<string, unknown>;
    const usage = parsed.usage;
    if (!usage || typeof usage !== "object") {
      return { usage: null, error: "usage_probe_missing_usage" };
    }
    return { usage: usage as Record<string, unknown>, error: null };
  } catch (error) {
    return { usage: null, error: `usage_probe_error=${compactError(error)}` };
  } finally {
    clearTimeout(timer);
  }
}

function parseSseLine(line: string): Record<string, unknown> | null {
  if (!line.startsWith("data:")) return null;
  const event = line.slice(5).trim();
  if (!event || event === "[DONE]") return null;
  try {
    return JSON.parse(event) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function extractEmittedText(chunk: Record<string, unknown>): { any: string; content: string; reasoning: string } {
  let content = "";
  let reasoning = "";
  const choices = Array.isArray(chunk.choices) ? chunk.choices : [];
  for (const choice of choices) {
    const delta = choice && typeof choice === "object" ? (choice as Record<string, unknown>).delta : null;
    if (!delta || typeof delta !== "object") continue;
    const deltaRecord = delta as Record<string, unknown>;
    const c = typeof deltaRecord.content === "string" ? deltaRecord.content : "";
    const r =
      typeof deltaRecord.reasoning === "string"
        ? deltaRecord.reasoning
        : typeof deltaRecord.reasoning_content === "string"
          ? deltaRecord.reasoning_content
          : "";
    content += c;
    reasoning += r;
  }
  return { any: content + reasoning, content, reasoning };
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function failureRow(task: Task, startedAt: string, status: number | null, error: string): ResultRow {
  return {
    suite: task.suite,
    provider: task.provider.name,
    model: task.provider.model,
    profile: task.profile.name,
    iteration: task.iteration,
    ok: false,
    status,
    started_at: startedAt,
    e2e_ms: null,
    ttft_ms: null,
    ttft_content_ms: null,
    ttfr_ms: null,
    ttfa_ms: null,
    first_delta_kind: null,
    first_delta_text: null,
    first_reasoning_text: null,
    first_content_text: null,
    full_reasoning_text: "",
    full_content_text: "",
    reasoning_effort_requested: task.provider.reasoningEffort ?? null,
    reasoning_effort_status: task.provider.reasoningEffort ? "sent" : "not_requested",
    concurrency: task.provider.concurrency ?? null,
    cache_mode: task.provider.cacheMode ?? null,
    median_itl_ms: null,
    chunks: 0,
    output_chars: 0,
    reasoning_chars: 0,
    prompt_tokens: null,
    output_tokens: null,
    total_tokens: null,
    effective_tok_s: null,
    token_usage_source: null,
    token_usage_error: null,
    error: compactError(error),
  };
}

export class BenchmarkRunner extends StatefulActor {
  async start(
    request: StartRequest,
    env: EnvLike = {},
  ): Promise<{ ok: boolean; total: number; startedAt: string; autoStart: boolean }> {
    const phases = request.phases?.length ? request.phases : null;
    if (!phases && !request.profiles?.length) throw new Error("profiles are required");
    if (!phases && !request.suites?.length) throw new Error("suites are required");
    if (phases) {
      for (const phase of phases) {
        if (!phase.name || !phase.profiles?.length || !phase.suites?.length) {
          throw new Error("each phase requires name, profiles, and suites");
        }
        if (phase.repeats < 1 || phase.batchSize < 1) {
          throw new Error("phase repeats and batchSize must be positive");
        }
      }
    }
    const repeats = request.repeats || 10;
    const planCells = phases ? buildPlanCells(phases) : undefined;
    const total = planCells
      ? planCells.reduce((sum, cell) => sum + cell.repeats, 0)
      : countTasks({ ...request, repeats });
    const state: RunState = {
      startedAt: nowIso(),
      repeats,
      timeoutMs: request.timeoutMs ?? 900_000,
      batchSize: request.batchSize ?? 1,
      autoStart: request.autoStart ?? true,
      tokenUsageProbe: request.tokenUsageProbe ?? false,
      total,
      cursor: 0,
      profiles: request.profiles ?? [],
      suites: request.suites ?? [],
      planCells,
      planCellIndex: planCells ? 0 : undefined,
      planIteration: planCells ? 0 : undefined,
    };
    await this.ctx.storage.put("state", state);
    await this.ctx.storage.put("results", []);
    await this.ctx.storage.put("secrets", env);
    if (state.autoStart) await this.scheduleNext(1);
    return { ok: true, total, startedAt: state.startedAt, autoStart: state.autoStart };
  }

  async status(): Promise<Record<string, unknown>> {
    const state = await this.ctx.storage.get<RunState>("state");
    const results = (await this.ctx.storage.get<ResultRow[]>("results")) ?? [];
    if (!state) return { ok: false, error: "No run has been started" };
    return {
      ok: true,
      startedAt: state.startedAt,
      total: state.total,
      completed: state.cursor,
      remaining: Math.max(0, state.total - state.cursor),
      successes: results.filter((row) => row.ok).length,
      failures: results.filter((row) => !row.ok).length,
      done: state.cursor >= state.total,
      autoStart: state.autoStart,
      batchSize: state.batchSize,
      tokenUsageProbe: state.tokenUsageProbe,
      finishedAt: state.finishedAt ?? null,
      lastAlarmAt: state.lastAlarmAt ?? null,
      currentPhase: state.planCells?.[state.planCellIndex ?? 0]?.phase ?? null,
      currentCell: state.planCells
        ? {
            index: state.planCellIndex ?? 0,
            total: state.planCells.length,
            iteration: state.planIteration ?? 0,
            provider: state.planCells[state.planCellIndex ?? 0]?.provider.name ?? null,
            profile: state.planCells[state.planCellIndex ?? 0]?.profile.name ?? null,
          }
        : null,
    };
  }

  async step(limit = 1, env: EnvLike = {}): Promise<Record<string, unknown>> {
    const state = await this.ctx.storage.get<RunState>("state");
    if (!state) return { ok: false, error: "No run has been started" };
    const results = (await this.ctx.storage.get<ResultRow[]>("results")) ?? [];
    const batch: ResultRow[] = [];
    let tasks: Task[];
    let stop: number;
    if (state.planCells) {
      const cellIndex = state.planCellIndex ?? 0;
      const cell = state.planCells[cellIndex];
      if (!cell) return { ok: true, processed: 0, completed: state.cursor, total: state.total, done: true };
      const iteration = state.planIteration ?? 0;
      const count = Math.min(Math.max(1, limit), cell.repeats - iteration);
      tasks = Array.from({ length: count }, (_, offset) => ({
        suite: cell.suite,
        provider: cell.provider,
        profile: cell.profile,
        iteration: iteration + offset + 1,
      }));
      stop = state.cursor + count;
    } else {
      stop = Math.min(state.total, state.cursor + Math.max(1, limit));
      tasks = Array.from(
        { length: stop - state.cursor },
        (_, offset) => taskAtIndex(state, state.cursor + offset),
      );
    }
    batch.push(
      ...(await Promise.all(
        tasks.map((task) => streamChat(task, env, state.timeoutMs, state.tokenUsageProbe)),
      )),
    );
    results.push(...batch);
    state.cursor = stop;
    if (state.planCells) {
      const cell = state.planCells[state.planCellIndex ?? 0];
      state.planIteration = (state.planIteration ?? 0) + batch.length;
      if (cell && state.planIteration >= cell.repeats) {
        state.planCellIndex = (state.planCellIndex ?? 0) + 1;
        state.planIteration = 0;
      }
    }
    await this.ctx.storage.put("state", state);
    await this.ctx.storage.put("results", results);
    if (state.cursor >= state.total && !state.finishedAt) {
      state.finishedAt = nowIso();
      await this.ctx.storage.put("state", state);
    }
    return {
      ok: true,
      processed: batch.length,
      completed: state.cursor,
      total: state.total,
      done: state.cursor >= state.total,
      last: batch[batch.length - 1] ?? null,
    };
  }

  async kick(): Promise<Record<string, unknown>> {
    const state = await this.ctx.storage.get<RunState>("state");
    if (!state) return { ok: false, error: "No run has been started" };
    if (state.cursor >= state.total) return { ok: true, done: true };
    await this.scheduleNext(1);
    return { ok: true, scheduled: true, completed: state.cursor, total: state.total };
  }

  async alarm(): Promise<void> {
    const state = await this.ctx.storage.get<RunState>("state");
    if (!state || state.cursor >= state.total) return;
    state.lastAlarmAt = nowIso();
    await this.ctx.storage.put("state", state);
    const secrets = (await this.ctx.storage.get<EnvLike>("secrets")) ?? {};
    const planCell = state.planCells?.[state.planCellIndex ?? 0];
    await this.step(planCell?.batchSize ?? state.batchSize, secrets);
    const updated = await this.ctx.storage.get<RunState>("state");
    if (updated && updated.autoStart && updated.cursor < updated.total) {
      await this.scheduleNext(1);
    }
  }

  async results(): Promise<ResultRow[]> {
    return (await this.ctx.storage.get<ResultRow[]>("results")) ?? [];
  }

  async resultsPage(offset = 0, limit = 25): Promise<{ rows: ResultRow[]; offset: number; nextOffset: number | null; total: number }> {
    const rows = await this.results();
    const safeOffset = Math.max(0, Math.floor(offset));
    const safeLimit = Math.min(100, Math.max(1, Math.floor(limit)));
    const page = rows.slice(safeOffset, safeOffset + safeLimit);
    const nextOffset = safeOffset + page.length < rows.length ? safeOffset + page.length : null;
    return { rows: page, offset: safeOffset, nextOffset, total: rows.length };
  }

  async resultsCsv(): Promise<string> {
    const rows = await this.results();
    const header = RESULT_FIELDS.join(",");
    const body = rows.map((row) => RESULT_FIELDS.map((field) => csvEscape(row[field])).join(","));
    return [header, ...body].join("\n");
  }

  private async scheduleNext(delayMs: number): Promise<void> {
    const storage = this.ctx.storage as unknown as {
      setAlarm?: (time: number | Date) => Promise<void>;
    };
    if (!storage.setAlarm) {
      throw new Error("Stateful Actor alarms are not available in this runtime");
    }
    await storage.setAlarm(Date.now() + delayMs);
  }
}
