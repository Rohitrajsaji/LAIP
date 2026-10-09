import { readFile } from "node:fs/promises";
import { parseRuntime, publicHealth } from "./runtime";

export async function backendHealth(): Promise<{ status: "ready" | "unavailable" | "ok" }> {
  try {
    const env = { ...process.env };
    if (env.LAIP_SERVICE_TOKEN_FILE) {
      if (env.LAIP_SERVICE_TOKEN) throw new Error("Select one token source");
      env.LAIP_SERVICE_TOKEN = (await readFile(env.LAIP_SERVICE_TOKEN_FILE, "utf8")).trim();
    }
    const config = parseRuntime(env);
    const result = await fetch(`${config.apiUrl}/api/v1/health/ready`, {
      headers: { Authorization: `Bearer ${config.serviceToken}` },
      signal: AbortSignal.timeout(2500),
      cache: "no-store",
    });
    if (!result.ok) return { status: "unavailable" };
    const body: unknown = await result.json();
    const data = body && typeof body === "object" && "data" in body
      ? (body as { data: unknown }).data : body;
    return publicHealth(data);
  } catch {
    return { status: "unavailable" };
  }
}
