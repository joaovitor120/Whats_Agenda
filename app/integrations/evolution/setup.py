"""Configure without printing QR codes, credentials or webhook response bodies."""

import asyncio

from app.core.config import settings
from app.integrations.evolution.client import EvolutionClient


async def main():
    client = EvolutionClient()
    await client.create_instance_if_needed()
    base = settings.evolution_webhook_base_url or settings.public_base_url
    await client.configure_webhook(f"{base.rstrip('/')}/webhooks/evolution")
    print("Webhook configurado. Consulte o QR pela rota administrativa protegida.")


if __name__ == "__main__":
    asyncio.run(main())
