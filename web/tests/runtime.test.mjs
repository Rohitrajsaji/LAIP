import assert from "node:assert/strict";
import test from "node:test";
import { parseRuntime, publicHealth } from "../src/lib/runtime.ts";

const env = { LAIP_API_URL: "http://api:8000", LAIP_SERVICE_TOKEN: "a".repeat(40) };
test("AI is disabled and API credentials stay out of public status", () => {
  const config = parseRuntime(env);
  assert.equal(config.aiEnabled, false);
  assert.equal(config.embeddingsEnabled, false);
  assert.deepEqual(publicHealth({ status: "ready", token: "secret", database_url: "private" }), { status: "ready" });
});
test("remote API locations, model enablement and missing credentials fail closed", () => {
  for (const patch of [
    { LAIP_API_URL: "https://example.com" },
    { LAIP_API_URL: "http://api:8000/override" },
    { LAIP_AI_ENABLED: "true" },
    { LAIP_EMBEDDINGS_ENABLED: "true" },
    { LAIP_MODEL_ENDPOINT: "http://private-model" },
    { LAIP_SERVICE_TOKEN: "short" },
    { LAIP_SERVICE_TOKEN: "" },
  ]) assert.throws(() => parseRuntime({ ...env, ...patch }));
});
test("malformed backend status cannot masquerade as readiness", () => {
  assert.throws(() => publicHealth({ status: "unknown" }));
  assert.throws(() => publicHealth(null));
});
