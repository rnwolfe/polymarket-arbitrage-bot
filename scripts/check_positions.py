# save as check_positions.py
import asyncio
from rarb.executor.async_clob import AsyncClobClient
from rarb.config import get_settings

async def check_positions():
    settings = get_settings()
    client = AsyncClobClient(
        private_key=settings.private_key.get_secret_value(),
        api_key=settings.poly_api_key,
        api_secret=settings.poly_api_secret.get_secret_value(),
        api_passphrase=settings.poly_api_passphrase.get_secret_value() if settings.poly_api_passphrase else "",
        proxy_url=settings.get_socks5_proxy_url(),
    )
    positions = await client.get_positions()
    print("Open Positions:")
    for p in positions:
        # Add this line inside the loop
        print(f"  Full Data: {p}")
        print(f"  Market: {p.get('market', {}).get('question', 'Unknown')}")
        print(f"  Token: {p.get('tokenID', 'N/A')}")
        print(f"  Size: {p.get('size', 0)}")
        print(f"  Side: {p.get('side', 'N/A')}")
        print(f"  Price: {p.get('price', 0)}")
        print("---")
    await client.close()

asyncio.run(check_positions())
