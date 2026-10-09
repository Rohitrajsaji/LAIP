import json
import os
import subprocess
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from test_persistence import db as db_fixture
from test_retrieval import published

from laip.read_service import ReadService

db = db_fixture


def test_real_stdio_entity_uses_readonly_postgres_and_matches_api_service(db, tmp_path):
    repo, bundle = published(db)
    arguments = {
        "schema_version": "0.1.0",
        "snapshot_id": "snap_retrieval",
        "entity_id": bundle["entities"][0]["entity_id"],
    }
    frames = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "fixture", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "laip_entity",
                "arguments": arguments,
            },
        },
    ]
    schema = db.execute("SELECT current_schema()").fetchone()[0]
    endpoint = urlsplit(os.environ["LAIP_TEST_DATABASE_URL"])
    query = parse_qsl(endpoint.query) + [("options", f"-csearch_path={schema}")]
    environment = {k: v for k, v in os.environ.items() if not k.startswith("LAIP_")}
    environment.update(
        LAIP_DATABASE_URL=urlunsplit(endpoint._replace(query=urlencode(query))),
        LAIP_SERVICE_TOKEN="synthetic-fixture-only-" + "a" * 32,
        LAIP_ARTIFACT_ROOT=str(tmp_path),
        LAIP_MCP_NAMESPACE=repo.namespace,
    )
    result = subprocess.run(
        [sys.executable, "-m", "laip.mcp"],
        input=b"".join(json.dumps(frame).encode() + b"\n" for frame in frames),
        capture_output=True,
        timeout=15,
        env=environment,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stderr == b""
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2]
    tool = replies[1]["result"]
    assert not tool["isError"]
    assert tool["structuredContent"]["data"] == ReadService(repo).call("entity", arguments)["data"]
