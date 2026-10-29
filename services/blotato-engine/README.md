# Blotato Self-Hosted Engine (Option A)

A high-performance, drop-in replacement for Blotato's unified social media API and MCP server. Built with FastAPI, SQLite/SQLAlchemy, and APScheduler.

Designed for two goals:
1. **Zero-cost personal engine** for your own AI agents (`chief-bot`, `nandini-bot`, Claude, ChatGPT, n8n).
2. **Commercial micro-SaaS** to sell API keys, post quotas, and MCP subscriptions to creators and agencies.

---

## ⚡ Key Features

- **100% Blotato REST Compatibility**: Exposes identical `/v2/posts`, `/v2/schedules`, `/v2/analytics`, `/v2/users/me/accounts`, and `/v2/credits` endpoints.
- **Native JSON-RPC MCP Server**: Connect Claude, ChatGPT, Cursor, and Hermes directly to `http://localhost:8400/mcp`.
- **Multi-Tenant API Key Billing**: Issue keys with per-plan credit quotas (`starter`, `pro`, `enterprise`) and hourly rate limits.
- **Background Scheduler**: Automatic scheduled post dispatcher running every 15 seconds.
- **Cross-Platform Connectors**:
  - **Meta Graph API**: Instagram Reels, Carousels, Stories, and Facebook Pages.
  - **X (Twitter) API v2**: Tweets, media uploads, thread support.
  - **LinkedIn API**: UGC posts and company page publishing.
  - **Bluesky ATProto**: Open protocol publishing with zero approval barriers.
  - **Composio / Mock Bridge**: Seamless fallback to Composio or local simulation mode.

---

## 📂 Project Structure

```
blotato-engine/
├── blotato_engine/
│   ├── config.py             # Settings, environment, master keys
│   ├── database.py           # Async SQLite engine & auto-seeding
│   ├── models.py             # SQLAlchemy models & Pydantic schemas
│   ├── auth.py               # blotato-api-key validator & credit gate
│   ├── scheduler.py          # Background post dispatcher (APScheduler)
│   ├── connectors/           # Instagram, X, LinkedIn, Bluesky, Mock
│   ├── api/                  # /v2/posts, /v2/accounts, /v2/billing
│   ├── mcp/server.py         # JSON-RPC MCP server
│   └── main.py               # FastAPI application
├── scripts/
│   ├── create_api_key.py     # CLI to mint new subscriber API keys
│   └── run_server.py         # Server launcher
└── tests/
    └── test_engine.py        # End-to-end verification suite
```

---

## 🚀 Quick Start

### 1. Run the Test Suite
```bash
uv run python tests/test_engine.py
```

### 2. Start the Server
```bash
uv run python scripts/run_server.py
```
- Interactive Swagger UI: `http://localhost:8400/docs`
- MCP Server: `http://localhost:8400/mcp`
- REST Base: `http://localhost:8400/v2`

---

## 💰 How to Monetize & Share

You can sell subscriptions or access to your engine. The built-in billing manager tracks usage and cuts off accounts when credits expire.

### Mint a new API key for a client:
```bash
uv run python scripts/create_api_key.py --client "Acme Marketing Agency" --plan pro --credits 5000
```

The script outputs a ready-to-send setup card:
```
✨ NEW BLOTATO CLIENT API KEY ISSUED
Client Name:       Acme Marketing Agency
Plan:              PRO
Credits Allotted:  5000
Rate Limit:        3600 req/hr
API Key:           blt_pro_7f8a9b1c2d3e...
------------------------------------------------------------
Client Connection Setup:
  Header:          blotato-api-key: blt_pro_7f8a9b1c2d3e...
  REST Endpoint:   http://<YOUR-SERVER-IP>:8400/v2
  MCP Endpoint:    http://<YOUR-SERVER-IP>:8400/mcp
```

### Pricing models you can charge:
1. **$19–$29 / month Flat**: Unlimited or 1,000 posts/month (undercutting Blotato's $29 starting tier).
2. **Pay-as-you-go**: $0.02 per published post.
3. **Agency Whitelabel**: $99 / month for unlimited accounts + custom branding.

---

## 🤖 Connecting Your AI Agents

### In Claude / Cursor / Hermes (MCP Config)
Add to your client configuration:
```json
{
  "mcpServers": {
    "blotato": {
      "url": "http://localhost:8400/mcp",
      "headers": {
        "blotato-api-key": "blt_master_venu_secret_2026"
      }
    }
  }
}
```

### In n8n or Make
In your HTTP Request node or custom Blotato community node:
- **Base URL**: `http://localhost:8400`
- **Header**: `blotato-api-key: <YOUR-KEY>`
- Use identical Blotato paths (`/v2/posts`, `/v2/users/me/accounts`).
