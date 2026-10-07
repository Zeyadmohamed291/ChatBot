"""Start a real local HTTP server, exercise it, then stop it."""

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = secrets.token_urlsafe(40)
    env = os.environ | {
        "AI_SERVICE_TOKEN": token,
        "ENVIRONMENT": "development",
        "PLANNER_MODE": "demo",
        "BACKEND_MODE": "mock",
        "REDIS_URL": "",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False
        ) as client:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Smoke server exited before readiness")
                try:
                    if client.get("/health/ready").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Smoke server did not become ready")
            headers = {
                "Authorization": f"Bearer {token}",
                "X-Session-Subject": "smoke-browser-session",
                "X-Customer-ID": "demo-customer",
            }
            session_id = None
            for message, expected in [
                ("مرحبا", "greeting"),
                ("عايز عطر تحت 160", "products"),
                ("Where is my order?", "number"),
                ("DEMO-1001", "order"),
            ]:
                response = client.post(
                    "/v1/chat", headers=headers, json={"message": message, "session_id": session_id}
                )
                response.raise_for_status()
                result = response.json()
                session_id = result["session_id"]
                assert not result["degraded"]
                if expected == "products":
                    assert result["product_cards"] and all(
                        float(c["price"]) <= 160 for c in result["product_cards"]
                    )
                if expected == "number":
                    assert result["requires_order_number"]
                if expected == "order":
                    assert result["order"]["status"] == "shipped"
                print(f"PASS {expected} ({result['language']}, demo mode)")
            assert client.delete(f"/v1/sessions/{session_id}", headers=headers).status_code == 204
            assert (
                client.post(
                    "/v1/chat", headers=headers, json={"message": "Hello", "session_id": session_id}
                ).status_code
                == 404
            )
            schema = client.get("/openapi.json").json()
            assert {p["name"] for p in schema["paths"]["/v1/chat"]["post"]["parameters"]} >= {
                "X-Session-Subject",
                "X-Customer-ID",
            }
            print("PASS session deletion and OpenAPI contract")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
