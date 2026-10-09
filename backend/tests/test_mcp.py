import io
import json

from test_persistence import db as db_fixture

from laip.mcp import MCPServer, serve

db = db_fixture


class Service:
    def __init__(self):
        self.calls = []

    def call(self, operation, arguments, cancelled=None):
        self.calls.append((operation, arguments))
        return {"schema_version": "0.1.0", "request_id": "fixture", "data": {}}


def message(method, params=None, request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def initialized(server):
    result = server.handle(
        message(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
    )
    assert result["result"]["capabilities"] == {"tools": {}}
    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})


def test_lifecycle_and_whitelist():
    server = MCPServer(Service())
    assert server.handle(message("tools/list"))["error"]["code"] == -32600
    initialized(server)
    tools = server.handle(message("tools/list"))["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "laip_entity",
        "laip_search",
        "laip_graph",
        "laip_evidence",
        "laip_context",
    ]
    assert all(tool["annotations"]["readOnlyHint"] for tool in tools)
    assert server.handle(message("commands/run"))["error"]["code"] == -32601
    assert (
        server.handle(message("tools/call", {"name": "laip_export", "arguments": {}}))["error"][
            "code"
        ]
        == -32602
    )


def test_frame_errors_are_bounded_and_redacted():
    source = io.BytesIO(b"{invalid}\n" + b"x" * 100 + b"\n")
    target = io.BytesIO()
    serve(source, target, Service(), max_frame_bytes=64)
    replies = [json.loads(line) for line in target.getvalue().splitlines()]
    assert [reply["error"]["code"] for reply in replies] == [-32700, -32600]
    assert b"invalid" not in target.getvalue()


def test_service_errors_and_invalid_arguments_do_not_escape():
    server = MCPServer(Service())
    initialized(server)
    reply = server.handle(
        message("tools/call", {"name": "laip_entity", "arguments": {"namespace": "secret"}})
    )
    assert reply["error"]["code"] == -32602
    assert "secret" not in json.dumps(reply)


def test_duplicate_json_batch_and_notification_are_safe():
    source = io.BytesIO(
        b'{"jsonrpc":"2.0","jsonrpc":"2.0"}\n[]\n{"jsonrpc":"2.0","method":"unknown"}\n'
    )
    target = io.BytesIO()
    serve(source, target, Service())
    replies = [json.loads(line) for line in target.getvalue().splitlines()]
    assert [reply["error"]["code"] for reply in replies] == [-32700, -32600]


def test_output_limit():
    server_input = (
        b'{"jsonrpc":"2.0","id":1,"method":"initialize",'
        b'"params":{"protocolVersion":"2025-11-25","capabilities":{},'
        b'"clientInfo":{"name":"test","version":"1"}}}\n'
    )
    target = io.BytesIO()
    serve(io.BytesIO(server_input), target, Service(), max_output_bytes=100)
    assert json.loads(target.getvalue())["error"]["message"] == "Output limit exceeded"


def entity_args():
    return {"schema_version": "0.1.0", "snapshot_id": "snapshot", "entity_id": "ent_" + "a" * 64}


class ValidService(Service):
    def call(self, operation, arguments, cancelled=None):
        self.calls.append((operation, arguments))
        return {
            "schema_version": "0.1.0",
            "request_id": "fixture",
            "data": {
                "snapshot_id": "snapshot",
                "system_namespace": "fixture",
                "run_id": "run",
                "entity_ids": [],
                "records": [],
                "boundaries": [],
                "truncated": False,
                "diagnostics": [],
            },
        }


def test_valid_call_and_output_validation():
    service = ValidService()
    server = MCPServer(service)
    initialized(server)
    reply = server.handle(
        message("tools/call", {"name": "laip_entity", "arguments": entity_args()})
    )
    assert reply["result"]["structuredContent"]["data"]["snapshot_id"] == "snapshot"
    assert service.calls == [("entity", entity_args())]
    bad = MCPServer(Service())
    initialized(bad)
    reply = bad.handle(message("tools/call", {"name": "laip_entity", "arguments": entity_args()}))
    assert reply["result"]["isError"] is True
    assert "structuredContent" not in reply["result"]


def test_cancellation_reaches_active_read():
    import threading

    started = threading.Event()
    finished = threading.Event()

    class SlowService(ValidService):
        def call(self, operation, arguments, cancelled=None):
            started.set()
            assert cancelled is not None
            while not cancelled():
                if finished.wait(0.01):
                    raise AssertionError("Cancellation absent")
            raise ValueError("secret database details")

    server = MCPServer(SlowService())
    initialized(server)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            server.handle(
                message("tools/call", {"name": "laip_entity", "arguments": entity_args()})
            )
        )
    )
    worker.start()
    assert started.wait(2)
    assert (
        server.handle(
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}}
        )
        is None
    )
    worker.join(2)
    finished.set()
    assert not worker.is_alive()
    assert results[0]["result"]["isError"] is True
    assert "secret" not in json.dumps(results)


def test_stdio_subprocess_protocol_only():
    import subprocess
    import sys

    code = (
        "from laip.mcp import serve; import sys; serve(sys.stdin.buffer,sys.stdout.buffer,object())"
    )
    frames = [
        message(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        ),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        message("tools/list", request_id=2),
    ]
    completed = subprocess.run(
        [sys.executable, "-c", code],
        input="".join(json.dumps(item) + "\n" for item in frames),
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    replies = [json.loads(line) for line in completed.stdout.splitlines()]
    assert len(replies) == 2
    assert len(replies[1]["result"]["tools"]) == 5


def test_database_service_uses_read_only_operator_namespace(monkeypatch):
    from types import SimpleNamespace

    from pydantic import SecretStr

    import laip.read_service
    from laip.mcp import DatabaseService

    commands = []
    connection_arguments = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, command):
            commands.append(command)

    def connect(dsn, **kwargs):
        connection_arguments.append((dsn, kwargs))
        return Connection()

    class Reader:
        def __init__(self, repository):
            assert repository.namespace == "authorized"

        def call(self, operation, arguments, cancelled=None):
            return {"operation": operation}

    monkeypatch.setattr("psycopg.connect", connect)
    monkeypatch.setattr(laip.read_service, "ReadService", Reader)
    settings = SimpleNamespace(database_url=SecretStr("private"), mcp_namespace="authorized")
    result = DatabaseService(settings).call("entity", entity_args())
    assert result == {"operation": "entity"}
    assert connection_arguments == [("private", {"autocommit": True, "connect_timeout": 3})]
    assert commands == ["SET default_transaction_read_only=on", "SET statement_timeout='30000ms'"]


def test_main_fails_closed_without_namespace(monkeypatch, capsys):
    from types import SimpleNamespace

    import laip.config
    from laip.mcp import main

    monkeypatch.setattr(laip.config, "Settings", lambda: SimpleNamespace(mcp_namespace=None))
    assert main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "explicit operator configuration" in captured.err


def test_real_snapshot_matches_shared_api_reads(db):
    from test_retrieval import published

    from laip.masking import POLICY
    from laip.read_service import ReadService

    repo, bundle = published(db)
    evidence_id = bundle["evidence"][0]["evidence_id"]
    repo.put_chunk(
        "mcp",
        "snap_retrieval",
        "customer password=secret validation",
        [evidence_id],
        POLICY,
        "v1",
        "none",
    )
    service = ReadService(repo)
    server = MCPServer(service)
    initialized(server)
    base = {"schema_version": "0.1.0", "snapshot_id": "snap_retrieval"}
    cases = {
        "entity": {**base, "entity_id": bundle["entities"][0]["entity_id"]},
        "search": {**base, "query": "customer"},
        "graph": {**base, "root_entity_ids": [bundle["dependencies"][0]["from_entity_id"]]},
        "evidence": {**base, "evidence_id": evidence_id},
        "context": {**base, "level": "system", "entity_ids": [], "max_context_tokens": 32768},
    }
    for operation, arguments in cases.items():
        expected = service.call(operation, arguments)
        response = server.handle(
            message("tools/call", {"name": "laip_" + operation, "arguments": arguments})
        )
        assert response["result"]["isError"] is False
        actual = response["result"]["structuredContent"]
        assert actual["schema_version"] == expected["schema_version"]
        assert actual["data"] == expected["data"]
        assert "password=secret" not in json.dumps(actual)
