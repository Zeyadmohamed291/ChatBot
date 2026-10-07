"""Exercise the running local service without displaying credentials."""

import asyncio

import httpx

from app.config import Settings


async def main():
    settings = Settings()
    headers = {
        "Authorization": f"Bearer {settings.ai_service_token.get_secret_value()}",
        "X-Session-Subject": "local-demo-browser",
        "X-Customer-ID": "demo-customer",
    }
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", headers=headers, timeout=30, trust_env=False
    ) as client:
        session_id = None
        for message in ["مرحبا", "عايز عطر تحت 160", "Where is my order?", "DEMO-1001"]:
            payload = {"message": message, "session_id": session_id}
            response = await client.post("/v1/chat", json=payload)
            response.raise_for_status()
            body = response.json()
            session_id = body["session_id"]
            print(message)
            print(body["answer"])
            for card in body["product_cards"]:
                print(f"  {card['name']}: {card['price']} {card['currency']}")
            print()
        await client.delete(f"/v1/sessions/{session_id}")


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
