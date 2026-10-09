"""Smoke the real localhost web/BFF and all container health checks."""

import json
import os
import subprocess
import urllib.request

port = int(os.environ.get("LAIP_WEB_PORT", "3000"))
base = f"http://127.0.0.1:{port}"
for endpoint in ["/api/health/live", "/api/health/ready"]:
    with urllib.request.urlopen(base + endpoint, timeout=10) as response:
        assert response.status == 200
        payload = json.load(response)
        assert payload["status"] == ("ready" if endpoint.endswith("ready") else "ok")
with urllib.request.urlopen(base, timeout=10) as response:
    html = response.read().decode()
    assert "BUSINESS RULES TO AI CONTEXT" in html and "Import source" in html
    assert "AI disabled" in html
with urllib.request.urlopen(base + "/api/analyst/workspace", timeout=15) as response:
    workspace = json.load(response)["data"]
    assert workspace["ai_enabled"] is False
    assert all(key in workspace for key in ("namespace", "imports", "runs", "exports"))
for service in ["frontend", "api", "worker", "database"]:
    cid = subprocess.check_output(
        ["docker", "compose", "ps", "--quiet", service], text=True
    ).strip()
    assert cid, f"{service} not running"
    state = subprocess.check_output(
        ["docker", "inspect", "--format", "{{.State.Health.Status}}", cid], text=True
    ).strip()
    assert state == "healthy", f"{service} health: {state}"
print("Startup smoke passed: four healthy services, localhost page/BFF, AI disabled.")
