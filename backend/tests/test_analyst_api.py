from fastapi.testclient import TestClient
from test_runtime import FakeProbe, settings

from laip.api import create_app


class StubAnalyst:
    def workspace(self):
        return {"namespace": "local:workspace", "ai_enabled": False, "snapshot_id": None}

    def accept_import(self, arguments):
        from laip.analyst_service import ImportRequest

        ImportRequest.model_validate(arguments)
        return {"import_id": "imp_test", "state": "queued"}


def test_analyst_routes_require_auth_and_validate_body(tmp_path):
    configured = settings(tmp_path)
    with TestClient(
        create_app(configured, FakeProbe(), analyst_factory=StubAnalyst),
        base_url="http://127.0.0.1",
    ) as client:
        assert client.get("/api/v1/workspace").status_code == 401
        headers = {"Authorization": "Bearer " + configured.service_token.get_secret_value()}
        response = client.get("/api/v1/workspace", headers=headers)
        assert response.status_code == 200
        assert response.json()["data"]["ai_enabled"] is False
        assert client.get("/api/v1/workspace?namespace=other", headers=headers).status_code == 400
        assert (
            client.get(
                "/api/v1/workspace", headers={**headers, "Origin": "https://evil.invalid"}
            ).status_code
            == 403
        )
        fixture = client.get("/api/v1/fixtures/analyst", headers=headers).json()["data"]
        response = client.post(
            "/api/v1/imports",
            headers=headers,
            json={
                "schema_version": "0.1.0",
                "archive_base64": fixture["archive_base64"],
                "manifest": fixture["manifest"],
            },
        )
        assert response.status_code == 200
        invalid = client.post(
            "/api/v1/imports", headers=headers, json={"schema_version": "0.1.0", "path": "/private"}
        )
        assert invalid.status_code == 400
        assert "/private" not in invalid.text
        duplicate = client.post(
            "/api/v1/runs",
            headers={**headers, "Content-Type": "application/json"},
            content='{"schema_version":"0.1.0","schema_version":"0.1.0"}',
        )
        assert duplicate.status_code == 400
