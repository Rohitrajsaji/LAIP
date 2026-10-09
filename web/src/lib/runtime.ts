export type RuntimeConfig = {
  apiUrl: string;
  serviceToken: string;
  aiEnabled: false;
  embeddingsEnabled: false;
};

export function parseRuntime(env: Record<string, string | undefined>): RuntimeConfig {
  for (const key of ["LAIP_AI_ENABLED", "LAIP_EMBEDDINGS_ENABLED"]) {
    if (env[key] && env[key].toLowerCase() !== "false") throw new Error("AI is disabled in the scaffold");
  }
  for (const key of ["LAIP_AI_ENDPOINT", "LAIP_MODEL_ENDPOINT", "LAIP_LLM_ENDPOINT", "LAIP_EMBEDDING_ENDPOINT"]) {
    if (env[key]) throw new Error("Model endpoints are unavailable in the scaffold");
  }
  const url = new URL(env.LAIP_API_URL ?? "http://api:8000");
  if (url.protocol !== "http:" || !["api", "localhost", "127.0.0.1"].includes(url.hostname)
    || url.pathname !== "/" || url.search || url.hash || url.username || url.password) {
    throw new Error("API must use a private local service URL");
  }
  const serviceToken = env.LAIP_SERVICE_TOKEN?.trim() ?? "";
  if (serviceToken.length < 32) throw new Error("A private service token is required");
  return { apiUrl: url.origin, serviceToken, aiEnabled: false, embeddingsEnabled: false };
}

export function publicHealth(body: unknown): { status: "ready" | "unavailable" | "ok" } {
  if (!body || typeof body !== "object") throw new Error("Invalid health response");
  const status = (body as Record<string, unknown>).status;
  if (status !== "ready" && status !== "unavailable" && status !== "ok") throw new Error("Invalid health response");
  return { status };
}
