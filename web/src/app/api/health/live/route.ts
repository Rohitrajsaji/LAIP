export function GET() {
  return Response.json({ status: "ok", prototype: true }, { headers: { "Cache-Control": "no-store" } });
}
