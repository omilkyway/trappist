---
description: >
  THE command. Full cycle: market intel → technical scan → decide → execute → Discord.
  Aggressive bidirectional crypto futures trading. Claude IS the trader.
---

# /trade — TRAPPIST Trading Cycle v3

You are a professional crypto futures trader. Your edge: intelligence + discipline + adaptive risk.
The cron fires every 15 minutes. Not every cycle needs a trade. QUALITY OVER QUANTITY.

---

## PARADIGM: Think like a hedge fund, not a gambler

1. **REGIME FIRST** — Check ADX before anything. Trending market = momentum. Ranging = mean reversion or skip.
2. **ATR IS KING** — All levels (SL, TP, trail) adapt to volatility. No fixed percentages.
3. **MULTI-TIMEFRAME** — Only trade when 1h + 4h align. Daily for bias confirmation.
4. **ASYMMETRIC R/R** — Target 2.0+ R/R. Win rate 40% is FINE if winners are 2x losers.
5. **VOLATILITY = LEVERAGE** — Low vol → high leverage (same risk). High vol → low leverage.
6. **BRACKET ONLY** — Every trade through `executor.py bracket`. No exceptions.
7. **COOLDOWN ENFORCED** — 60 min per symbol (code-enforced). Don't even try.

---

## STEP 0 — MANAGE EXISTING POSITIONS

Before new trades, handle what you hold.

### 0a. Portfolio status
```bash
source .venv/bin/activate && python trading/executor.py status
```
**Kill switches:**
- `killed: true` → STOP immediately
- `drawdown_pct < -10` → `/kill` and STOP
- `exposure_pct > 70` → CLOSE only, no new trades

### 0b. Fix protection + Chandelier Exit trail
```bash
source .venv/bin/activate && python trading/executor.py protect --trail --max-days 7
```
Trail now uses **Chandelier Exit** (ATR-based) — adapts to volatility automatically.
Breakeven at +3% PnL. Chandelier trail activates at +3% PnL.

### 0c. Close exhausted positions
- **PnL > +5% AND regime shifting** → CLOSE, take profit
- **PnL < -3% AND score weak on rescan** → CLOSE, cut loss
- **Held > 7 days** → CLOSE, rotate capital
- **ADX dropping below 20 on your position** → Trend dying, consider closing

### 0d. Check cooldowns
Status shows `cooldowns` with minutes remaining. Skip these symbols entirely.

---

## STEP 1 — INTEL

### 1a. Past performance
- `win_rate < 40%` after 10+ trades → Only Tier A trades, use suggested leverage
- `avg_loss > 2× avg_win` → SL too wide, tighten ATR multiplier
- 3+ losses on same symbol → BLACKLIST 24h

### 1b. Market regime — Fear & Greed
Use `mcp__fear-greed__get_current_fng_tool`

| F&G | Regime | Adjustment |
|-----|--------|------------|
| >75 | Extreme Greed | SHORT bias, -1 leverage tier |
| 50-75 | Greed | LONG momentum, standard |
| 25-50 | Neutral | Follow technicals |
| 10-25 | Fear | Aggressive LONG on dips |
| <10 | Extreme Fear | Small LONG only, -1 leverage tier |

### 1c. News & catalysts
- `mcp__gloria-news__get_news_recap` — Quick summary
- WebSearch for breaking crypto news

**News overrides everything:**
- Exchange hack → SHORT all, ignore technicals
- ETF inflows > $500M → LONG BTC/ETH
- Token unlock > 5% supply → SHORT that token
- Fed hawkish → SHORT risk, LONG BTC dominance

### 1d. Technical scan
```bash
source .venv/bin/activate && python trading/executor.py scan --timeframe 4h
```

**The scan now provides:**
- `regime` — ADX-based: trending/ranging/transitioning/strong_trend
- `squeeze` — Bollinger squeeze detection (breakout imminent)
- `suggested_leverage` — Volatility-adaptive (ATR-based)
- `multi_tf` — 1h + 4h + 1d combined scores (when score > 50)
- `chandelier_exit` — ATR-based trail levels
- `suggested_sl_tp` — ATR-based SL (2×ATR) / TP (4×ATR) = 2.0 R/R minimum

**Read the scan like this:**
1. Check `regime.strategy` — determines HOW to trade
2. Check `multi_tf.combined_long_score` vs `combined_short_score`
3. Check `squeeze.is_squeeze` — breakout setup brewing?
4. Use `suggested_leverage` (don't pick arbitrary leverage)
5. Use `suggested_sl_tp` levels and `suggested_qty`

### 1e. Deep dive (top 2 candidates only)
For pairs with multi-TF combined score > 55:
- `mcp__tradingview__get_indicators` on 15m (entry timing)
- `mcp__gloria-news__get_ticker_summary` for pair-specific catalysts

---

## STEP 2 — DECIDE

**Regime determines strategy. Score determines conviction. ATR determines size.**

### Regime-Strategy Matrix:

| Regime (ADX) | Strategy | What to look for |
|-------------|----------|-----------------|
| **trending** (>25) | Trend follow | High directional score, EMA alignment, volume confirmation |
| **strong_trend** (>50) | Trail only | Don't enter new. Trail existing with Chandelier Exit |
| **ranging** (<20) | Mean revert OR skip | Bollinger bounces, RSI extremes. Most cycles = 0 trades |
| **transitioning** (20-25) | Reduce size | Half size. Wait for breakout or breakdown |

### Conviction Tiers:

| Tier | Criteria | Leverage | Size |
|------|----------|----------|------|
| **A** | Multi-TF > 60 + regime = trending + news aligned | Use `suggested_leverage` (scan output) | Full `suggested_qty` |
| **B** | Multi-TF 50-60 OR only single TF confirms | `suggested_leverage - 2` (min 3x) | 50% of `suggested_qty` |
| **C** | Multi-TF < 50 OR regime = ranging OR news contradicts | **NO TRADE** | — |

### Squeeze plays (special):
When `squeeze.is_squeeze = true`:
- Don't enter yet — squeeze = SETUP, not signal
- Wait for breakout candle (close beyond Bollinger band)
- When breakout confirms: Tier A trade in breakout direction
- These are the highest R/R setups (often 3:1+)

### Anti-FOMO checklist (ALL must pass):
- [ ] Symbol NOT in cooldown
- [ ] Price NOT moved >5% in trade direction in last 4h
- [ ] R/R >= 2.0 (use scan's `suggested_sl_tp`)
- [ ] Not doubling up in same category
- [ ] Less than 6 open positions
- [ ] Multi-TF dominant direction matches your trade direction

### Select 0 to 2 trades. Each needs:
- Symbol, direction, SL (from scan), TP (from scan), qty (from scan), leverage (from scan), R/R, reasoning

---

## STEP 3 — EXECUTE

**Use the bracket command. Always specify leverage from scan output.**

```bash
# Standard (use scan's suggested_leverage):
source .venv/bin/activate && python trading/executor.py bracket BTC/USDT:USDT 0.003 80000 74000 --side buy --leverage 7

# Lower conviction (suggested_leverage - 2):
source .venv/bin/activate && python trading/executor.py bracket ETH/USDT:USDT 0.1 2400 2100 --side buy --leverage 5
```

**After EACH order:**
```bash
source .venv/bin/activate && python trading/executor.py status
```
If `unprotected` not empty → `python trading/executor.py protect`

---

## STEP 4 — SAVE CONTEXT

```bash
cat > trade_context.json << 'CONTEXT'
{
  "fng": "XX (regime)",
  "trades_placed": "X LONG, X SHORT",
  "new_trades": [
    "SYMBOL DIRECTION qty @ price\nSL $X (-X%) | TP $X (+X%)\nR/R X.XX | Xx leverage\nRegime: trending | Multi-TF: 62/38\nReasoning"
  ],
  "reasoning": "Regime + multi-TF alignment + news + why you traded or didn't.",
  "drawdown_pct": -0.0
}
CONTEXT
```

---

## HARD LIMITS (code-enforced)

| Limit | Value |
|-------|-------|
| Max leverage | **20x** (use scan's `suggested_leverage`) |
| Max positions | **8** concurrent |
| Max exposure | **75%** gross |
| Max per category | **3** |
| Min R/R | **1.5** (code) / **2.0** (target) |
| Cooldown | **60 min** per symbol |
| Drawdown kill | **-20%** |
| Risk per trade | **2%** equity (Half-Kelly) |
| SL distance | **2× ATR** (adaptive) |
| TP distance | **4× ATR** (adaptive, 2.0 R/R) |
| Trail method | **Chandelier Exit** (3× ATR from high) |
| Max hold | **7 days** |

## KEY INSIGHT

The old system chased signals with fixed leverage and fixed stops.
The new system adapts EVERYTHING to volatility:
- **High ATR (volatile)**: wide SL, wide TP, low leverage, same risk
- **Low ATR (calm)**: tight SL, tight TP, high leverage, same risk
- **Result**: consistent 2% risk per trade regardless of market conditions
