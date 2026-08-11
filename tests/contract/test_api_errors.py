from fastapi.testclient import TestClient


def test_validation_error_shape(client: TestClient) -> None:
    response = client.post("/api/v1/debug/strategic/reset", json={"unexpected": True})
    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["request_id"]
