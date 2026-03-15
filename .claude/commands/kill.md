---
description: Emergency close ALL positions and stop the bot
---

# KILL SWITCH

**Close every position, cancel every order, stop the bot.**

## Step 1 — Get current state
```bash
source .venv/bin/activate && python trading/executor.py status
```

## Step 2 — Close each position
For each open position:
```bash
source .venv/bin/activate && python trading/executor.py close SYMBOL
```

## Step 3 — Mark bot as killed
```bash
source .venv/bin/activate && python -c "
import json
state = json.load(open('state.json'))
state['killed'] = True
state['killed_at'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
json.dump(state, open('state.json', 'w'), indent=2)
print('Bot KILLED')
"
```

## Step 4 — Verify
```bash
source .venv/bin/activate && python trading/executor.py status
```

## Step 5 — Discord notification
```bash
source .env.local 2>/dev/null || source .env 2>/dev/null
curl -s -H "Content-Type: application/json" -X POST "$DISCORD_WEBHOOK_URL" \
  -d '{"embeds":[{"title":"🚨 TRAPPIST KILL SWITCH","color":16711680,"description":"All positions closed. Bot stopped.\nTo restart: edit state.json → killed: false"}]}'
```
