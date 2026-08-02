"""앱 기동과 health 엔드포인트 확인."""

from __future__ import annotations

from fastapi.testclient import TestClient

from main import app


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
