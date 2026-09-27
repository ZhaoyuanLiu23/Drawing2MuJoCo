"""Exercise the real ASGI app without running the robot pipelines."""

import asyncio

from httpx import ASGITransport, AsyncClient

from app.main import app


def test_health_endpoint():
    async def request_health():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.get("/api/health")

    response = asyncio.run(request_health())

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert {(path, method.upper()) for path, operations in app.openapi()["paths"].items() for method in operations} == {
        ("/api/health", "GET"),
        ("/api/jobs", "POST"),
        ("/api/jobs/{job_id}/preview", "GET"),
        ("/api/jobs/{job_id}/generate-cad", "POST"),
        ("/api/jobs/{job_id}/cad/{artifact}", "GET"),
        ("/api/jobs/{job_id}/simulate", "POST"),
        ("/api/jobs/{job_id}/simulation/{run_id}/result.json", "GET"),
        ("/api/jobs/{job_id}/simulation/{run_id}/simulation.mp4", "GET"),
    }
