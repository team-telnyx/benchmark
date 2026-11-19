export { BenchmarkRunner } from "./benchmark";
import type { BenchmarkRunner } from "./benchmark";
import { env as runtimeEnv } from "@telnyx/edge-runtime";
import type { ActorNamespace, ActorStub, IdFromNameOptions } from "@telnyx/edge-runtime";

type BenchmarkStub = ActorStub &
  Pick<BenchmarkRunner, "start" | "status" | "step" | "kick" | "results" | "resultsPage" | "resultsCsv">;

interface BenchmarkNamespace extends ActorNamespace {
  idFromName(name: string, options?: IdFromNameOptions): BenchmarkStub;
}

interface Env {
  BENCHMARK: BenchmarkNamespace;
  SECRETS?: {
    get(handle: string): Promise<string>;
  };
  TELNYX_API_KEY?: string;
  TOGETHER_API_KEY?: string;
  FIREWORKS_API_KEY?: string;
  BASETEN_API_KEY?: string;
  MODAL_PROXY_TOKEN?: string;
}

function json(data: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(data, null, 2), {
    ...init,
    headers: {
      "content-type": "application/json; charset=utf-8",
      ...(init.headers ?? {}),
    },
  });
}

function notFound(): Response {
  return json({ error: "not found" }, { status: 404 });
}

function runIdFromPath(pathname: string): string | null {
  const match = pathname.match(/^\/runs\/([^/]+)\//);
  return match?.[1] ?? null;
}

async function secretsFromEnv(env: Env): Promise<Record<string, string | undefined>> {
  const names = [
    "TELNYX_API_KEY",
    "TOGETHER_API_KEY",
    "FIREWORKS_API_KEY",
    "BASETEN_API_KEY",
    "MODAL_PROXY_TOKEN",
  ] as const;
  const secrets: Record<string, string | undefined> = {};
  const secretStore = env.SECRETS ?? (runtimeEnv as unknown as Env).SECRETS;

  for (const name of names) {
    const direct = env[name];
    if (direct) {
      secrets[name] = direct;
      continue;
    }

    try {
      secrets[name] = await secretStore?.get(name);
    } catch {
      secrets[name] = undefined;
    }
  }

  return secrets;
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);

    if (url.pathname === "/health/liveness" || url.pathname === "/health/readiness") {
      return new Response("ok");
    }

    const runId = runIdFromPath(url.pathname);
    if (!runId) return notFound();
    const runner = env.BENCHMARK.idFromName(runId);

    if (url.pathname.endsWith("/start") && req.method === "POST") {
      const body = await req.json();
      return json(await runner.start(body, await secretsFromEnv(env)));
    }

    if (url.pathname.endsWith("/status") && req.method === "GET") {
      return json(await runner.status());
    }

    if (url.pathname.endsWith("/step") && req.method === "POST") {
      const limit = Number(url.searchParams.get("limit") ?? "1");
      return json(await runner.step(Number.isFinite(limit) ? limit : 1, await secretsFromEnv(env)));
    }

    if (url.pathname.endsWith("/kick") && req.method === "POST") {
      return json(await runner.kick());
    }

    if (url.pathname.endsWith("/results.json") && req.method === "GET") {
      return json(await runner.results());
    }

    if (url.pathname.endsWith("/results-page.json") && req.method === "GET") {
      const offset = Number(url.searchParams.get("offset") ?? "0");
      const limit = Number(url.searchParams.get("limit") ?? "25");
      return json(await runner.resultsPage(offset, limit));
    }

    if (url.pathname.endsWith("/results.csv") && req.method === "GET") {
      return new Response(await runner.resultsCsv(), {
        headers: { "content-type": "text/csv; charset=utf-8" },
      });
    }

    return notFound();
  },
};
