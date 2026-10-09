import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

from laip.config import Settings
from laip.runtime import DependencyProbe
from laip.worker import HEARTBEAT


async def check_worker(settings: Settings) -> bool:
    if not (await DependencyProbe(settings).check()).ready:
        return False
    try:
        data = json.loads((settings.artifact_root / HEARTBEAT).read_text())
        age = time.time() - float(data["observed_at"])
        return 0 <= age <= settings.worker_heartbeat_max_age and data["status"] in (
            "waiting_for_schema",
            "awaiting_handlers",
            "busy",
            "processed_job",
            "idle",
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def check_api(settings: Settings) -> bool:
    request = urllib.request.Request(
        "http://127.0.0.1:8000/api/v1/health/ready",
        headers={"Authorization": "Bearer " + settings.service_token.get_secret_value()},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return bool(response.status == 200)
    except (urllib.error.URLError, OSError):
        return False


def main() -> int:
    settings = Settings()
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    healthy = (
        check_api(settings)
        if mode == "api"
        else asyncio.run(check_worker(settings))
        if mode == "worker"
        else False
    )
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
