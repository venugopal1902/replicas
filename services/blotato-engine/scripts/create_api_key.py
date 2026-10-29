import asyncio
import argparse
import uuid
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from blotato_engine.database import AsyncSessionLocal, init_db
from blotato_engine.models import ApiKeyModel

async def issue_key(client_name: str, plan: str, credits: int):
    await init_db()
    
    token = f"blt_{plan}_{uuid.uuid4().hex[:24]}"
    rate_limit = 1800 if plan == "starter" else (3600 if plan == "pro" else 10000)
    
    async with AsyncSessionLocal() as session:
        key_record = ApiKeyModel(
            api_key=token,
            client_name=client_name,
            plan=plan,
            credits=credits,
            rate_limit_per_hour=rate_limit,
            is_active=True
        )
        session.add(key_record)
        await session.commit()
        await session.refresh(key_record)
        
    print("\n" + "="*60)
    print("✨ NEW BLOTATO CLIENT API KEY ISSUED")
    print("="*60)
    print(f"Client Name:       {client_name}")
    print(f"Plan:              {plan.upper()}")
    print(f"Credits Allotted:  {credits if credits != -1 else 'Unlimited'}")
    print(f"Rate Limit:        {rate_limit} req/hr")
    print(f"API Key:           {token}")
    print("-"*60)
    print("📋 Client Connection Setup:")
    print("  Header:          blotato-api-key: " + token)
    print("  REST Endpoint:   http://<YOUR-IP-OR-DOMAIN>:8400/v2")
    print("  MCP Endpoint:    http://<YOUR-IP-OR-DOMAIN>:8400/mcp")
    print("="*60 + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mint a new client API key for Blotato engine")
    parser.add_argument("--client", required=True, help="Name of client or subscriber")
    parser.add_argument("--plan", choices=["starter", "pro", "enterprise"], default="starter", help="Pricing tier")
    parser.add_argument("--credits", type=int, default=1000, help="Initial credit balance (-1 for unlimited)")
    
    args = parser.parse_args()
    asyncio.run(issue_key(args.client, args.plan, args.credits))
