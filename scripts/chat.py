"""Interactive terminal chat for experimenting with Loulou AI."""

import asyncio
import sys

import httpx

from app.config import Settings


async def main():
    settings = Settings()
    headers = {
        "Authorization": f"Bearer {settings.ai_service_token.get_secret_value()}",
        "X-Session-Subject": "interactive-cli-session",
        "X-Customer-ID": "demo-customer",
    }
    url = "http://127.0.0.1:8000"

    print("==================================================")
    print("✨ Loulou AI — Interactive CLI Chat | محادثة تجريبية ✨")
    print(f"Active Mode: PLANNER={settings.planner_mode} | BACKEND={settings.backend_mode}")
    print("Type your message and press Enter. To exit type: exit, quit, or خروج")
    print("==================================================\n")

    async with httpx.AsyncClient(base_url=url, headers=headers, timeout=30, trust_env=False) as client:
        # Check health first
        try:
            health = await client.get("/health/ready")
            if health.status_code != 200:
                print(f"⚠️ Warning: Server status code: {health.status_code}")
        except Exception:
            print("❌ Server is not currently running!")
            print("Please start the server first in another terminal with:")
            print("  python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000\n")
            return

        session_id = None
        while True:
            try:
                user_msg = input("You (أنت): ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_msg:
                continue
            if user_msg.lower() in {"exit", "quit", "خروج"}:
                break

            payload = {"message": user_msg, "session_id": session_id}
            try:
                res = await client.post("/v1/chat", json=payload)
                if res.status_code != 200:
                    print(f"❌ Error ({res.status_code}): {res.text}")
                    continue
                data = res.json()
                session_id = data.get("session_id")

                print(f"\n🤖 Assistant (المساعد): {data.get('answer')}")

                # Show product cards if any
                cards = data.get("product_cards", [])
                if cards:
                    print("\n🛍️ Recommended Products (المنتجات المقترحة):")
                    for idx, c in enumerate(cards, 1):
                        price = f"{c.get('price')} {c.get('currency', 'SAR')}"
                        print(f"   {idx}. {c.get('name')} | Price (السعر): {price}")

                # Show store cards if any
                stores = data.get("store_cards", [])
                if stores:
                    print("\n🏬 Stores (المتاجر):")
                    for idx, s in enumerate(stores, 1):
                        print(f"   {idx}. {s.get('name')} - {s.get('description', '')}")

                # Show order status if any
                order = data.get("order")
                if order:
                    print(f"\n📦 Order Status (حالة الطلب) [{order.get('order_number')}]: {order.get('status')}")

                # Show quick replies
                replies = data.get("quick_replies", [])
                if replies:
                    print(f"\n💡 Quick Replies (اقتراحات): {' | '.join(replies)}")

                print("-" * 50)
            except Exception as e:
                print(f"❌ Connection error: {e}")

        if session_id:
            try:
                await client.delete(f"/v1/sessions/{session_id}")
            except Exception:
                pass
        print("\nSession ended successfully. مع السلامة!")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    asyncio.run(main())
