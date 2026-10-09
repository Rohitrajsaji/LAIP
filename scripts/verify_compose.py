"""Check the rendered Compose boundary without printing secret/config values."""

import json
import subprocess

config = json.loads(
    subprocess.check_output(
        ["docker", "compose", "config", "--format", "json"], text=True
    )
)
services = config["services"]
assert set(services) == {"frontend", "api", "worker", "database", "migrate"}, (
    "Unexpected service"
)
for name, service in services.items():
    ports = service.get("ports", [])
    if name == "frontend":
        assert len(ports) == 1 and ports[0]["host_ip"] == "127.0.0.1", (
            "Frontend must bind loopback"
        )
    else:
        assert not ports, f"{name} must not publish a host port"
for name in ["frontend", "api", "worker"]:
    env = services[name]["environment"]
    assert str(env["LAIP_AI_ENABLED"]).lower() == "false"
    assert str(env["LAIP_EMBEDDINGS_ENABLED"]).lower() == "false"
assert config["networks"]["private"]["internal"] is True
assert set(services["frontend"]["networks"]) == {"private", "ingress"}
assert all(
    set(services[name]["networks"]) == {"private"}
    for name in ["api", "worker", "database", "migrate"]
)
assert all("artifacts" not in str(v) for v in services["frontend"].get("volumes", []))
assert services["api"]["healthcheck"] and services["worker"]["healthcheck"]
print(
    "Compose boundary verified: frontend loopback only, private services/storage, AI off."
)
