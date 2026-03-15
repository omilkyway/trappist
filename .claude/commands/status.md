---
description: Quick portfolio dashboard
---

Run and display the full status:

```bash
source .venv/bin/activate && python trading/executor.py status
```

Then check protection:
```bash
source .venv/bin/activate && python trading/executor.py protect --dry-run
```

Display a clean summary with:
- Mode (TESTNET/LIVE)
- Equity and exposure %
- Each position with PnL and protection status
- Unprotected positions (URGENT if any)
- Total unrealized PnL
