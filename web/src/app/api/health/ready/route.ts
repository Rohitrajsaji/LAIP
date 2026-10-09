import { backendHealth } from "../../../../lib/backend";
export const dynamic = "force-dynamic";
export async function GET() {
  const health = await backendHealth();
  return Response.json(health, { status: health.status === "ready" ? 200 : 503, headers: { "Cache-Control": "no-store" } });
}
