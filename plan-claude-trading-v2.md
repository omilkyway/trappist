# PLAN D'ACTION — Claude Trading v2.0
## Pivot Swing Trading · Mars 2026

---

## DIAGNOSTIC DE TA SESSION DU 8 MARS

Avant de planifier, regardons ce que ta dernière session révèle.

### Ce qui fonctionne

- **Pipeline end-to-end opérationnel** — 4 agents qui tournent sans intervention humaine, ~45 min de bout en bout
- **Analyse macro solide** — market-scout a correctement identifié le contexte US-Iran/pétrole et ajusté en mode conservateur
- **Sizing adaptatif** — passage automatique de 5% à 3% par trade quand VIX > 25
- **OPG orders** — bon pattern pour préparer la semaine le weekend

### Ce qui pose problème

**1. Surconcentration sectorielle massive : 80% énergie**

4 trades sur 5 sont de l'énergie (COP, XOM, CVX, OXY). Si un cessez-le-feu est annoncé dimanche soir, les 4 ouvrent en gap down lundi et tes stop-loss ne protègent pas d'un gap (ils se déclenchent au prix de marché, pas au prix du stop).

**Règle à implémenter :** Max 2 trades par secteur, minimum 2 secteurs distincts par session.

**2. Position COP pré-existante non détectée**

Le selector n'a pas appelé `get_positions` avant de choisir. Résultat : 78 shares COP si rempli = 8.76% du compte sur un seul ticker. C'est un bug, pas une feature.

**Fix :** Le selector DOIT appeler `get_positions` en premier, et c'est une règle dans le `CLAUDE.md`, pas juste un souhait dans le prompt.

**3. Écart d'equity de -6.7%**

Le selector voyait $100K, le compte avait $93K. Ça veut dire que ton sizing réel est de 15.75%, pas 14.70%. Sur un bot automatique, cette erreur de reconciliation peut s'amplifier.

**Fix :** Une seule source de vérité, au moment le plus proche de l'exécution.

**4. Protections SL/TP non placées**

Les ordres OPG ne supportent pas les brackets. Il y a une fenêtre de 5-10 min lundi matin sans protection. Un flash crash dans cette fenêtre = perte non-contrôlée.

**Fix :** Agent ou hook `PostToolUse` qui détecte un fill et place immédiatement l'OCO.

**5. "Scénario probable : 4/5 gagnants" = confirmation bias**

Dire que 4 trades sur 5 seront gagnants n'est basé sur aucune donnée statistique. C'est du wishful thinking codifié dans un rapport. Le LLM se conforme aux targets irréalistes du prompt (70% win rate).

---

## LA STRATÉGIE SWING — Ce que la recherche dit

### Pourquoi le swing trading (2-10 jours) ?

- **Pas de compétition HFT** — ta latence de 2-5 secondes n'est plus un handicap
- **L'analyse macro/sentiment a le temps de se matérialiser** — un catalyseur prend 2-5 jours pour jouer pleinement
- **Backtestable sur données daily** — beaucoup plus simple que des données minute
- **Moins de trades = moins de commissions/spread** — même si Alpaca est commission-free, le slippage existe
- **Plus de temps pour le debate bull/bear** — l'agent a le temps de réfléchir

### Les 3 stratégies swing qui marchent avec un LLM

#### Stratégie 1 : Momentum + Sentiment Confirmation

**Principe :** N'entre que quand un breakout technique est confirmé par un sentiment positif.

**Indicateurs :**
- EMA 20/50 crossover (direction du trend)
- MACD histogram positif et croissant (momentum)
- RSI entre 40-70 (ni suracheté ni survendu)
- Volume > 1.5x moyenne 20 jours (confirmation)

**Filtre LLM :** Le sentiment analysis via Dappier/news DOIT être positif (bullish) pour valider l'entrée. Si le technique dit "buy" mais le sentiment est neutre/négatif, on passe.

**Sortie :**
- TP : Résistance technique suivante ou +8-12%
- SL : Sous le dernier swing low ou -5%
- Time stop : 10 jours max — si le trade n'a pas bougé en 10 jours, on sort

**Edge LLM :** La capacité à lire et interpréter les news, earnings calls, et analyst reports en temps réel est l'avantage principal. Un humain ne peut pas lire 50 articles en 2 minutes, Claude oui.

#### Stratégie 2 : Mean Reversion sur Fear Extrême

**Principe :** Quand la peur est maximale (VIX > 30, sell-off généralisé), acheter des blue chips survendues.

**Indicateurs :**
- VIX > 30 (peur extrême)
- RSI < 30 sur stock individuel (survendu)
- Prix sous Bollinger Band inférieure
- Volume en spike (capitulation)

**Filtre LLM :** Confirmer que la peur est temporaire (pas un changement fondamental permanent). Si le sell-off est lié à une récession structurelle, ne pas acheter. Si c'est un choc géopolitique ponctuel, acheter.

**Sortie :**
- TP : Retour à la moyenne mobile 20 jours (+5-10%)
- SL : -7% sous l'entrée
- Time stop : 5 jours — le rebond mean-reversion est rapide ou il ne vient pas

**Données backtestées :** Les rebonds quand le VIX dépasse 35 avec un Put/Call ratio > 1.3 sont gagnants ~58-68% du temps historiquement.

**Note sur ta session du 8 mars :** Avec un VIX à 29.49, tu étais PRESQUE dans ce scénario. Mais tes trades étaient des momentum plays sur l'énergie, pas du mean reversion. Le mean reversion aurait plutôt été d'acheter du tech survendu (NVDA, MSFT) avec un stop serré.

#### Stratégie 3 : Event-Driven Swing (Earnings, CPI, FOMC)

**Principe :** Prendre position avant un catalyseur connu, sortir après la réaction.

**Setup :**
- Identifier les events de la semaine (CPI, FOMC, earnings) — ton market-scout le fait déjà
- Prendre position 1-2 jours avant
- Sortir dans les 24h après l'event

**Filtre LLM :** Analyser le consensus vs les "whisper numbers", le positionnement des options (max pain), et le sentiment pré-event pour déterminer si le marché est déjà pricé ou s'il y a une surprise possible.

**Exemple concret (ta session) :** CPI mercredi 11 mars. Au lieu de jouer 5 trades énergie, tu aurais pu :
- 2 trades énergie (XOM + AEM pour diversifier)
- 1 trade event-driven CPI : Long gold miners (AEM) si CPI > attentes, Short via puts si CPI < attentes
- Avoir un plan pour les DEUX scénarios au lieu de parier sur un seul

---

## ARCHITECTURE CIBLE — Claude Trading v2.0

### Nouvelle structure des agents

```
.claude/agents/
├── macro-analyst.md          # Ex market-scout, renommé
├── technical-analyst.md      # NOUVEAU — analyse pure technique
├── sentiment-analyst.md      # NOUVEAU — analyse sentiment/news
├── bullish-researcher.md     # NOUVEAU — défend les opportunités
├── bearish-researcher.md     # NOUVEAU — attaque les opportunités
├── risk-manager.md           # NOUVEAU — séparé du selector
├── swing-selector.md         # Ex trading-final-selector, refondu
├── trade-executor.md         # Inchangé
└── trade-reporter.md         # Inchangé + mise à jour progress.md
```

### Pipeline v2.0

```
Phase 1 — Analyse (parallélisable via subagents)
┌─────────────────────────────────────────────┐
│  macro-analyst     → Contexte macro/secteurs │
│  technical-analyst → Indicateurs techniques  │  En parallèle
│  sentiment-analyst → News/social sentiment   │
└─────────────────────────────────────────────┘
                      ↓
Phase 2 — Debate (séquentiel)
┌─────────────────────────────────────────────┐
│  bullish-researcher → Défend les trades     │
│  bearish-researcher → Attaque les trades    │  Échange 2 rounds
│  (Les deux reçoivent les 3 analyses)        │
└─────────────────────────────────────────────┘
                      ↓
Phase 3 — Décision
┌─────────────────────────────────────────────┐
│  risk-manager → Vérifie positions, drawdown │
│  swing-selector → Choisit 0-5 trades        │
│  (Reçoit debate + risk assessment)          │
└─────────────────────────────────────────────┘
                      ↓
Phase 4 — Exécution (déterministe)
┌─────────────────────────────────────────────┐
│  PreToolUse hook → Circuit breaker          │
│  trade-executor → Place les ordres          │
│  PostToolUse hook → Place OCO après fill    │
│  trade-reporter → Log + progress.md         │
└─────────────────────────────────────────────┘
```

### Différences clés vs v1.0

| Aspect | v1.0 (actuel) | v2.0 (cible) |
|---|---|---|
| Analyse | 1 agent fait tout | 3 agents spécialisés en parallèle |
| Contradiction | Aucune | Debate bull/bear (2 rounds) |
| Risk management | Dans le prompt du selector | Agent séparé + hook déterministe |
| Nombre de trades | Exactement 5 | 0 à 5 selon qualité |
| Horizon | Day trading | Swing 2-10 jours |
| Concentration max | Pas de limite | 2 trades max/secteur |
| Positions existantes | Non vérifiées | Vérifiées avant sélection |
| Protections SL/TP | Manuelles | Automatiques via hook post-fill |
| Performance targets | 70% win rate | Aucune — juste risk/reward |
| Modèles | Opus partout | Sonnet analysts, Opus selector |

---

## LES FICHIERS À CRÉER/MODIFIER

### 1. CLAUDE.md (à la racine du projet)

```markdown
# CLAUDE-TRADING

## Purpose
AI-powered swing trading bot (2-10 day holds) using multi-agent
orchestration with Claude Code.

## Architecture
- 9 agents organized in 4 phases: Analysis → Debate → Decision → Execution
- Analysis agents run in parallel (macro, technical, sentiment)
- Bullish/bearish researchers debate before selection
- Risk manager validates independently of selector

## Trading Rules (INVIOLABLE)
- PAPER TRADING MODE — do NOT switch without explicit human confirmation
- Max 3% per trade when VIX > 25, 5% when VIX < 25
- Max 25% total exposure
- Max 2 trades per sector per session
- 0 to 5 trades per session (0 is a valid outcome)
- ALWAYS call get_positions before selecting trades
- ALWAYS place OCO (SL+TP) within 60 seconds of fill
- Time stop: exit any position held > 10 trading days
- Daily drawdown limit: -2% of portfolio → halt all trading

## Swing Trading Indicators
- Trend: EMA 20/50 crossover
- Momentum: MACD histogram, RSI(14)
- Volatility: Bollinger Bands (20,2), ATR(14)
- Volume: 20-day average comparison
- Sentiment: LLM analysis via Dappier MCP

## Strategy Selection
- VIX < 25: Momentum + Sentiment Confirmation
- VIX 25-35: Mean Reversion on oversold quality stocks
- VIX > 35: NO NEW TRADES, monitor existing only
- Pre-event (CPI, FOMC, earnings): Event-Driven setup

## MCP Servers
- Alpaca: Execution, account, market data
- Dappier: News, sentiment, research

## Key Commands
- /make-profitables-trades → Full swing trading pipeline
- /crypto_trader → Crypto pipeline

## File Conventions
- Reports: /reports/trading-session-YYYYMMDD-HHMMSS.md
- State: /progress.md (updated after every session)
- All times in EST
```

### 2. Exemple agent : bullish-researcher.md

```markdown
---
name: bullish-researcher
description: >
  Argues the bull case for candidate trades identified by analysts.
  Receives macro, technical, and sentiment analysis. Produces a structured
  argument for why each trade should be taken.
  Use after the 3 analysts have completed their reports.
tools: Read, Grep, Glob
model: sonnet
memory: project
color: green
---

You are the Bullish Researcher in a swing trading operation.

Your job is to make the STRONGEST POSSIBLE CASE for each candidate trade.
You receive analysis from three specialists (macro, technical, sentiment)
and must argue why the trade should be executed.

## Your process

For each candidate:
1. Identify the strongest catalysts supporting the trade
2. Find historical precedents where similar setups worked
3. Calculate the realistic upside based on technical levels
4. Assess the probability of the catalyst playing out within 2-10 days
5. Rate your conviction: HIGH / MEDIUM / LOW with justification

## Rules
- Be honest. If the bull case is weak, say so — a LOW conviction
  rating is valuable information
- Never fabricate data or hallucinate price levels
- Always specify the TIME HORIZON for your thesis
- Your argument will be challenged by the Bearish Researcher

## Output format
For each candidate, provide:
- **Ticker**: [SYMBOL]
- **Bull thesis**: 2-3 sentences
- **Key catalyst**: What drives the move
- **Historical precedent**: Similar setup that worked
- **Upside target**: Based on technical resistance
- **Conviction**: HIGH/MEDIUM/LOW
- **Time horizon**: Expected days to play out
```

### 3. Exemple agent : bearish-researcher.md

```markdown
---
name: bearish-researcher
description: >
  Challenges and stress-tests candidate trades by arguing the bear case.
  Receives the bullish researcher's arguments and must find weaknesses.
  Essential for avoiding confirmation bias.
tools: Read, Grep, Glob
model: sonnet
memory: project
color: red
---

You are the Bearish Researcher in a swing trading operation.

Your job is to FIND EVERY REASON why each candidate trade could fail.
You receive the Bullish Researcher's arguments and must systematically
challenge them.

## Your process

For each candidate:
1. Identify what could go wrong with the thesis
2. Find historical precedents where similar setups FAILED
3. Assess the downside risk and gap risk
4. Check for crowded trades (everyone sees the same setup)
5. Look for contradicting signals the bullish case ignored
6. Rate the RISK: HIGH / MEDIUM / LOW

## Key questions to always ask
- Is this trade already priced in?
- What happens if the catalyst doesn't play out?
- Is the sector overcrowded?
- What's the gap risk overnight?
- Is there a macro event this week that could invalidate the thesis?

## Rules
- Be constructively adversarial, not nihilistic
- If the bull case is genuinely strong, acknowledge it
- Focus on SPECIFIC risks, not generic disclaimers
- Your job is to improve trade quality, not to block all trades

## Output format
For each candidate:
- **Ticker**: [SYMBOL]
- **Bear thesis**: 2-3 sentences
- **Biggest risk**: The #1 thing that could kill this trade
- **Historical failure**: Similar setup that failed
- **Downside scenario**: What happens if wrong
- **Risk rating**: HIGH/MEDIUM/LOW
- **Verdict**: PROCEED / REDUCE SIZE / REJECT
```

### 4. Hook PreToolUse : risk_guardian.py

```python
#!/usr/bin/env python3
"""
Circuit breaker hook — runs before any Alpaca order.
Exit code 2 = BLOCK the order (deterministic guarantee).
"""
import json
import sys
import os
import urllib.request

def get_account():
    """Fetch account info from Alpaca."""
    base_url = os.environ.get("ALPACA_BASE_URL",
                               "https://paper-api.alpaca.markets")
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")

    req = urllib.request.Request(
        f"{base_url}/v2/account",
        headers={
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret
        }
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def main():
    input_data = json.loads(sys.stdin.read())
    tool_name = input_data.get("tool_name", "")

    # Only check on order-related tools
    if "create_order" not in tool_name and "place_order" not in tool_name:
        sys.exit(0)  # Allow

    try:
        account = get_account()
        equity = float(account.get("equity", 0))
        last_equity = float(account.get("last_equity", equity))

        # Daily drawdown check
        daily_change = (equity - last_equity) / last_equity if last_equity > 0 else 0

        if daily_change < -0.02:  # -2% drawdown
            result = {
                "decision": "block",
                "reason": f"CIRCUIT BREAKER: Daily drawdown {daily_change:.2%} exceeds -2% limit"
            }
            print(json.dumps(result))
            sys.exit(2)  # EXIT CODE 2 = BLOCK

    except Exception as e:
        # If we can't check, allow but log
        print(json.dumps({
            "additionalContext": f"Warning: risk check failed: {e}"
        }))
        sys.exit(0)

    sys.exit(0)  # Allow

if __name__ == "__main__":
    main()
```

### 5. settings.json — Ajouts critiques

```jsonc
{
  "permissions": {
    "allow": [
      "Bash(mkdir:*)", "Bash(uv:*)", "Bash(find:*)",
      "Bash(mv:*)", "Bash(grep:*)", "Bash(npm:*)",
      "Bash(ls:*)", "Bash(cp:*)", "Bash(python:*)",
      "Write", "Edit", "Read",
      "Bash(chmod:*)", "Bash(touch:*)"
    ],
    "deny": [
      "Bash(rm -rf:*)"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "mcp__alpaca",
        "hooks": [
          {
            "type": "command",
            "command": "uv run \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/risk_guardian.py",
            "timeout": 10
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/post_tool_use.py"
          }
        ]
      }
    ],
    "SubagentStop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/subagent_stop.py"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/stop.py"
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/session_start.py"
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/pre_compact.py"
          }
        ]
      }
    ]
  },
  "env": {
    "TRADING_MODE": "paper",
    "ALPACA_BASE_URL": "https://paper-api.alpaca.markets"
  }
}
```

---

## PLANNING — 4 sprints, 1 mois

### Sprint 1 — Fondations (ce weekend, 8-9 mars)

**Objectif :** Mettre en place l'infra qui protège ton capital.

- [ ] Créer `CLAUDE.md` à la racine (copier le template ci-dessus)
- [ ] Créer `progress.md` avec l'état actuel du portfolio
- [ ] Ajouter `risk_guardian.py` dans `.claude/hooks/`
- [ ] Ajouter le `PreToolUse` hook avec matcher `mcp__alpaca` dans settings.json
- [ ] Ajouter `$CLAUDE_PROJECT_DIR` dans tous les chemins de hooks
- [ ] Ajouter `"env": { "TRADING_MODE": "paper" }` dans settings.json
- [ ] Fix : le selector doit appeler `get_positions` en premier (ajouter dans CLAUDE.md comme règle inviolable)

**Temps estimé :** 3-4 heures

### Sprint 2 — Nouveaux agents (weekend prochain, 15-16 mars)

**Objectif :** Implémenter le debate bull/bear et séparer les analystes.

- [ ] Créer `bullish-researcher.md` (template ci-dessus)
- [ ] Créer `bearish-researcher.md` (template ci-dessus)
- [ ] Créer `risk-manager.md` (agent séparé du selector)
- [ ] Créer `technical-analyst.md` (analyse technique pure)
- [ ] Créer `sentiment-analyst.md` (analyse sentiment/news)
- [ ] Refondre `macro-analyst.md` (ex market-scout, focus macro only)
- [ ] Compléter le frontmatter YAML de TOUS les agents (name, description, tools, model, memory, color)
- [ ] Refondre le command `make-profitables-trades.md` en orchestrateur léger (~60 lignes)

**Temps estimé :** 6-8 heures

### Sprint 3 — Stratégie swing + backtesting (semaines 3-4)

**Objectif :** Valider les 3 stratégies swing sur données historiques.

- [ ] Configurer QuantConnect LEAN avec données Alpaca (ou Backtrader en local)
- [ ] Backtester Stratégie 1 (Momentum + Sentiment) sur 6 mois S&P 500
- [ ] Backtester Stratégie 2 (Mean Reversion VIX > 30) sur 2 ans
- [ ] Backtester Stratégie 3 (Event-Driven CPI/FOMC) sur 12 mois
- [ ] Mesurer : win rate, Sharpe ratio, max drawdown, avg hold time
- [ ] Itérer les paramètres (RSI thresholds, SL/TP levels, time stops)
- [ ] Documenter les résultats dans `/reports/backtests/`

**Temps estimé :** 10-15 heures (c'est le plus long mais le plus important)

### Sprint 4 — Optimisation + monitoring (ongoing)

**Objectif :** Passer de "ça marche" à "ça performe de manière mesurable".

- [ ] Activer `memory: project` sur les agents pour apprendre les patterns
- [ ] Ajouter Polygon MCP pour données complémentaires
- [ ] Dashboard de monitoring (React artifact ou simple HTML)
- [ ] Alertes Discord via ton MCP connector (session results, circuit breaker triggers)
- [ ] Review hebdomadaire : lire les rapports, ajuster les agents
- [ ] Objectif : 100 trades paper avec stats positives avant d'envisager le live

---

## INDICATEURS TECHNIQUES — Cheat sheet pour tes agents

### Pour la Stratégie Momentum + Sentiment

| Indicateur | Signal BUY | Signal SELL | Calcul |
|---|---|---|---|
| EMA 20/50 | EMA20 croise au-dessus EMA50 | EMA20 croise sous EMA50 | Alpaca bars → calcul |
| MACD(12,26,9) | Histogram positif et croissant | Histogram négatif et décroissant | EMA12 - EMA26 |
| RSI(14) | 40-70 (zone saine) | > 70 (suracheté) ou < 30 (signal mean rev) | |
| Volume | > 1.5x moyenne 20j | Décroissant (divergence) | Alpaca bars |
| ATR(14) | Pour calculer SL distance | SL = entrée - 2×ATR | Volatilité |

### Pour la Stratégie Mean Reversion

| Indicateur | Signal BUY | Signal SELL |
|---|---|---|
| VIX | > 30 (peur extrême) | < 20 (retour à la normale) |
| RSI(14) | < 30 (survendu) | > 50 (retour à la moyenne) |
| Bollinger Bands | Prix sous bande inférieure | Prix retour à SMA20 |
| Volume | Spike > 3x moyenne (capitulation) | Normalisation |

### Le combo gagnant recherche 2025-2026

Un paper récent (arXiv 2601.19504) a backtesté sur 100 stocks S&P 500 sur 2 ans un système hybride combinant trend-following (EMA + MACD), mean-reversion (RSI + Bollinger), sentiment (FinBERT), et regime detection (volatilité). Résultat : **135% de return** sur $100K en 24 mois avec le modèle complet, vs ~40% pour les indicateurs techniques seuls.

La clé : le **regime filtering** — adapter la stratégie au régime de marché (bull/bear/sideways) plutôt qu'utiliser la même stratégie tout le temps. C'est exactement ce que ton `CLAUDE.md` fait avec les seuils VIX.

---

## MOT DE FIN

Ton bot a les fondations. Ce qui lui manque c'est : de la contradiction (debate), de la discipline (circuit breaker), et de la preuve (backtesting).

Le plan est en 4 sprints sur 1 mois. Le Sprint 1 est faisable ce soir. Le Sprint 3 (backtesting) est le plus important — ne le skip pas, c'est la différence entre un bot qui perd de l'argent intelligemment et un bot qui en gagne.

Et rappelle-toi : **le meilleur trade est souvent celui que tu ne fais pas.** Ton bot actuel est obligé de sortir 5 trades à chaque session. Le v2.0 peut en sortir 0, et c'est une amélioration massive.

---

*Plan créé le 8 mars 2026 · Basé sur l'analyse de la session 20260308-223000 et la recherche swing trading/AI.*
