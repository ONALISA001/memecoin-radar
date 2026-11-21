# FOMO MCP server (read-only)

Lets Claude (Claude Desktop, Claude Code) read your MemeCoin Radar app while you chat:

> "What's hot on the launch radar?" · "Give me the full report on CBUCKS" ·
> "Which priority coins passed in the last 4h and how did earlier picks do?" ·
> "How are my paper strategies doing? Any supervisor proposals?"

**Read-only.** Every tool is a GET against the app's API, signed in as a visitor. It cannot
change settings, add alerts, trade, delete or spend anything. All trading in the app is simulated.

## Tools

| Tool | What it returns |
|---|---|
| `market_overview` | Market phase, breadth, volume, top narratives |
| `launch_radar` | New / Bonding / Graduated / Hot pump.fun launches, seconds after creation |
| `launch_candles` | OHLCV candles for a launch (1m / 5m / 15m / 1h) |
| `priority_coins` | Coins passing every gate, early candidates, track record |
| `trending_tokens` | Tracked coins ranked by trend, with risk and momentum |
| `new_coins` | Recently launched tracked coins (fresh pools, Telegram, promoted) |
| `distribution_watch` | Coins under selling pressure / whale distribution |
| `intel_feed` | Momentum, distribution and liquidity events |
| `narratives`, `news` | Trending themes and headlines |
| `search_tokens` | Find a coin by symbol, name or address |
| `token_report` | Everything on one coin: scores, risk, holders, on-chain checks, forensics, creator, verdict, AI analysis |
| `price_history` | Price points (15m … 7d) |
| `verdicts`, `verdict_scorecard` | Latest verdicts and how accurate they turned out |
| `paper_trading_status`, `paper_trades` | Simulated account, open positions, trade history |
| `strategy_lab` | Strategies side by side, ranking and supervisor proposals |
| `watchlist` | The app's watchlist with intelligence |

## Install (Windows, once)

```powershell
cd C:\Users\user\Documents\memecoin-radar\mcp-server
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Connect it

**Claude Desktop:** Settings → Developer → Edit Config, then add this to
`%APPDATA%\Claude\claude_desktop_config.json` and restart Claude Desktop:

```json
{
  "mcpServers": {
    "fomo": {
      "command": "C:\\Users\\user\\Documents\\memecoin-radar\\mcp-server\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\user\\Documents\\memecoin-radar\\mcp-server\\fomo_mcp.py"]
    }
  }
}
```

If the file already has an `mcpServers` section, add only the `"fomo": {…}` entry inside it.

**Claude Code** (any terminal):

```powershell
claude mcp add fomo --scope user -- C:\Users\user\Documents\memecoin-radar\mcp-server\.venv\Scripts\python.exe C:\Users\user\Documents\memecoin-radar\mcp-server\fomo_mcp.py
```

## Settings (optional)

| Variable | Default | Meaning |
|---|---|---|
| `FOMO_API_URL` | `https://memecoinrada.netlify.app/api` | Point it at another deployment, e.g. `http://localhost:8000/api` |
| `FOMO_TIMEOUT` | `45` | Seconds per request; the free Render server can take a while to wake |

In Claude Desktop, set these with an `"env": {"FOMO_API_URL": "…"}` entry next to `"args"`.

## Notes

- Answers are trimmed (no images, raw blobs or empty fields) to save Claude's context.
- If the server is waking up, a tool may say it timed out; asking again usually works.
- It uses the same rate limit as the website (240 requests a minute); `token_report` makes 9.
