# Cloud Claude Code — Playbook Scaleway Serverless Jobs

> Zero VM. Zero idle. Pay-per-second.
> Datacenter Paris. 100% CLI.
> Coût infra estimé : **~0€/mois** (free tier).
> Temps total : ~30 min.

---

## Pourquoi Serverless Jobs (et pas un VPS)

| Critère | VPS 24/7 (DEV1-S) | Serverless Jobs |
|---|---|---|
| **Coût/mois** | 6,42€ (même idle) | ~0€ (free tier couvre le volume) |
| **Maintenance** | Patches, fail2ban, SSH hardening | Zéro. Image Docker immuable |
| **Scaling** | Manuel (stop → upgrade → start) | Change les resources dans la job def |
| **Persistance** | Locale (disque) | Object Storage S3 |
| **Cold start** | 0 (toujours allumé) | ~30s (pull image + boot container) |
| **Sécurité** | Toi qui gère le firewall | Scaleway gère l'isolation |

**Billing Serverless Jobs :**

- Mémoire : 0,10€ / 100 000 GB-s — free tier **400 000 GB-s/mois**
- vCPU : 1,00€ / 100 000 vCPU-s — free tier **200 000 vCPU-s/mois**
- Stockage éphémère : gratuit

**Calcul pour ton use case :**

```
3 jobs/jour × 20 jours ouvrés = 60 runs/mois
Durée moyenne : ~15 min (900s) par run
Config : 2 vCPU / 2 GB RAM

vCPU-s  = 60 × 900 × 2 = 108 000  (free tier : 200 000) ✅
GB-s    = 60 × 900 × 2 = 108 000  (free tier : 400 000) ✅

→ Coût infra Scaleway = 0€
→ Même à 30 min/run : 216 000 vCPU-s → léger dépassement = ~0,16€/mois
```

---

## Architecture

```
                    Kubuntu locale
                    ├─ docker build & push (1x ou CI)
                    ├─ scw jobs definition start (trigger manuel)
                    └─ aliases monitoring

┌──────────────────────────────────────────────────────────────┐
│              Scaleway — Paris (fr-par)                        │
│                                                              │
│  ┌─────────────────────────┐   ┌──────────────────────────┐ │
│  │   Container Registry     │   │   Secret Manager          │ │
│  │   rg.fr-par.scw.cloud/  │   │   ANTHROPIC_API_KEY       │ │
│  │   claude-trading:latest  │   │   DISCORD_WEBHOOK_URL     │ │
│  └───────────┬─────────────┘   │   ALPACA_API_KEY           │ │
│              │                  │   ALPACA_SECRET_KEY         │ │
│              ▼                  └──────────┬───────────────┘ │
│  ┌──────────────────────────────────────────┐                │
│  │         Serverless Jobs                   │                │
│  │                                           │                │
│  │  Job 1: morning-run                       │                │
│  │    cron: 15 15 * * 1-5 (Europe/Paris)     │                │
│  │    2 vCPU / 2048 MB / timeout 30min       │                │
│  │    command: /app/scripts/entrypoint.sh     │                │
│  │    args: morning                           │                │
│  │                                           │                │
│  │  Job 2: midday-check                      │                │
│  │    cron: 0 18 * * 1-5 (Europe/Paris)      │                │
│  │    1 vCPU / 1024 MB / timeout 15min       │                │
│  │    args: midday                            │                │
│  │                                           │                │
│  │  Job 3: eod-review                        │                │
│  │    cron: 30 21 * * 1-5 (Europe/Paris)     │                │
│  │    2 vCPU / 2048 MB / timeout 20min       │                │
│  │    args: eod                               │                │
│  └──────────────┬───────────────────────────┘                │
│                 │                                             │
│  ┌──────────────▼───────────────────────────┐                │
│  │       Object Storage (S3)                 │                │
│  │       s3://claude-trading/                │                │
│  │       ├─ progress.md                      │                │
│  │       ├─ reports/                         │                │
│  │       └─ logs/                            │                │
│  └──────────────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────┘
                    │
              Discord DM
              (webhook rapports)
```

---

## PHASE 0 — Prérequis (5 min)

### Installer le CLI Scaleway + outils

```bash
# CLI Scaleway
brew install scw
# ou : curl -s https://raw.githubusercontent.com/scaleway/scaleway-cli/master/scripts/get.sh | sh

# Docker (si pas déjà installé)
sudo apt install -y docker.io
sudo usermod -aG docker $USER
# → re-login pour que le groupe prenne effet

# AWS CLI (pour Object Storage S3)
sudo apt install -y awscli

# Vérifier
scw version
docker --version
```

### Initialiser le CLI

```bash
# Crée ta clé API : https://console.scaleway.com/iam/api-keys
scw init
# → Access Key, Secret Key, default zone=fr-par-1, region=fr-par

# Vérifier
scw account project list
```

### Créer le bucket S3 pour la persistance

```bash
# Créer le bucket
scw object bucket create name=claude-trading region=fr-par

# Configurer awscli pour Scaleway S3
cat >> ~/.aws/credentials << 'CREDS'
[scaleway]
aws_access_key_id = SCWXXXXXXXXXXXXXXXXX
aws_secret_access_key = xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
CREDS

cat >> ~/.aws/config << 'CONF'
[profile scaleway]
region = fr-par
endpoint_url = https://s3.fr-par.scw.cloud
CONF

# Alias pour simplifier
alias s3scw="aws --profile scaleway"

# Test
s3scw s3 ls s3://claude-trading/
```

---

## PHASE 1 — Construire l'image Docker (10 min)

### Structure du projet

```bash
mkdir -p ~/claude-trading-serverless/{scripts,config}
cd ~/claude-trading-serverless
```

### Dockerfile

```bash
cat > Dockerfile << 'DOCKERFILE'
FROM node:20-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    curl jq git ca-certificates awscli \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Dedicated user
RUN useradd -m -s /bin/bash claude-bot

# App files
COPY --chown=claude-bot:claude-bot scripts/ /app/scripts/
COPY --chown=claude-bot:claude-bot config/CLAUDE.md /app/CLAUDE.md
COPY --chown=claude-bot:claude-bot config/.mcp.json /app/.mcp.json
COPY --chown=claude-bot:claude-bot config/settings.json /home/claude-bot/.claude/settings.json

RUN chmod +x /app/scripts/*.sh

USER claude-bot
WORKDIR /app

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
DOCKERFILE
```

### CLAUDE.md

```bash
cat > config/CLAUDE.md << 'CLAUDEMD'
# Claude Trading Bot — Context

## Identity
Tu es un swing trader AI. Marché US (NYSE/NASDAQ).
Alpaca MCP pour les ordres. Dappier MCP pour les news/sentiment.

## Rules (NON NÉGOCIABLES)
- MAX 5% du portfolio par trade (3% si VIX > 25)
- MAX 2 positions par secteur
- Ratio R/R minimum 1.5:1
- Si drawdown journalier > -2% → AUCUN nouvel ordre
- 0 trade est un résultat VALIDE
- Toujours vérifier get_positions + get_account AVANT tout

## Process — Morning Run (/morning-run)
1. get_account → equity, buying power, drawdown
2. get_positions → état des positions ouvertes
3. Analyse macro via Dappier (sentiment, catalyseurs)
4. Scan top movers pre-market via Alpaca
5. Analyse technique (EMA 20/50, RSI 14, MACD, Bollinger)
6. Sélection 0-3 swing trades (horizon 2-10 jours)
7. Exécution bracket orders (entry + stop-loss + take-profit)
8. Rapport → reports/ et update progress.md

## Process — Midday Check
1. get_positions → P&L courant par position
2. Distance au stop / take-profit
3. Time stop : > 10 jours → close
4. Si drawdown > -1.5% → tighten stops à -3%
5. Update progress.md

## Process — EOD Review
1. Résumé journalier (P&L, trades exécutés, leçons)
2. Update progress.md avec stats cumulées
3. Générer rapport dans reports/
CLAUDEMD
```

### .mcp.json

```bash
cat > config/.mcp.json << 'MCP'
{
  "mcpServers": {
    "alpaca": {
      "command": "npx",
      "args": ["-y", "@anthropic-ai/alpaca-mcp-server"],
      "env": {
        "ALPACA_API_KEY": "${ALPACA_API_KEY}",
        "ALPACA_SECRET_KEY": "${ALPACA_SECRET_KEY}",
        "ALPACA_BASE_URL": "${ALPACA_BASE_URL}"
      }
    },
    "dappier": {
      "command": "npx",
      "args": ["-y", "@anthropic-ai/dappier-mcp-server"],
      "env": {
        "DAPPIER_API_KEY": "${DAPPIER_API_KEY}"
      }
    }
  }
}
MCP
```

> ⚠️ Adapter les noms de packages npx selon tes MCP servers réels.

### settings.json (permissions Claude Code)

```bash
mkdir -p config
cat > config/settings.json << 'SETTINGS'
{
  "permissions": {
    "allow": [
      "Read",
      "Grep",
      "Glob",
      "mcp__alpaca__*",
      "mcp__dappier__*"
    ],
    "deny": [
      "Bash(*)",
      "Write",
      "Edit"
    ]
  },
  "env": {
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  }
}
SETTINGS
```

### Scripts

#### entrypoint.sh — Router principal

```bash
cat > scripts/entrypoint.sh << 'SCRIPT'
#!/bin/bash
set -euo pipefail

RUN_TYPE="${1:-morning}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
S3_BUCKET="s3://claude-trading"

# ─── AWS config pour Scaleway S3 ───
export AWS_ACCESS_KEY_ID="${SCW_S3_ACCESS_KEY}"
export AWS_SECRET_ACCESS_KEY="${SCW_S3_SECRET_KEY}"
export AWS_DEFAULT_REGION="fr-par"
export AWS_ENDPOINT_URL="https://s3.fr-par.scw.cloud"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ─── Sync state from S3 ───
log "Downloading state from S3..."
mkdir -p /app/reports /app/logs
aws s3 cp "$S3_BUCKET/progress.md" /app/progress.md 2>/dev/null || echo "# Trading Progress" > /app/progress.md
aws s3 sync "$S3_BUCKET/reports/" /app/reports/ 2>/dev/null || true

# ─── Route to correct run type ───
case "$RUN_TYPE" in
  morning)
    log "=== Starting morning trading session ==="
    PROMPT="/morning-run"
    MAX_TURNS=30
    ;;
  midday)
    log "=== Starting midday position check ==="
    PROMPT="Execute midday position check per CLAUDE.md. Update progress.md."
    MAX_TURNS=20
    ;;
  eod)
    log "=== Starting EOD review ==="
    PROMPT="Execute EOD review per CLAUDE.md. Save full report to reports/eod-$TIMESTAMP.md. Update progress.md with cumulative stats."
    MAX_TURNS=25
    ;;
  *)
    log "Unknown run type: $RUN_TYPE"
    exit 1
    ;;
esac

# ─── Execute Claude Code ───
cd /app
claude -p "$PROMPT" \
    --dangerously-skip-permissions \
    --model claude-sonnet-4-6 \
    --max-turns "$MAX_TURNS" \
    --output-format stream-json \
    --verbose \
    2>&1 | tee "/app/logs/${RUN_TYPE}-${TIMESTAMP}.log"

EXIT_CODE=${PIPESTATUS[0]}
log "Session completed (exit: $EXIT_CODE)"

# ─── Sync state back to S3 ───
log "Uploading state to S3..."
aws s3 cp /app/progress.md "$S3_BUCKET/progress.md"
aws s3 sync /app/reports/ "$S3_BUCKET/reports/"
aws s3 sync /app/logs/ "$S3_BUCKET/logs/"

# ─── Discord notification ───
if [[ -n "${DISCORD_WEBHOOK_URL:-}" ]]; then
    EMOJI="📊"
    [[ "$RUN_TYPE" == "morning" ]] && EMOJI="🌅"
    [[ "$RUN_TYPE" == "midday" ]] && EMOJI="☀️"

    SUMMARY=$(tail -10 /app/progress.md 2>/dev/null || echo "No progress yet")
    # Tronquer à 1900 chars (limite Discord)
    SUMMARY="${SUMMARY:0:1900}"

    curl -sf -H "Content-Type: application/json" \
      -d "{\"content\": \"$EMOJI **${RUN_TYPE^} Run $TIMESTAMP** (exit: $EXIT_CODE)\n\`\`\`\n$SUMMARY\n\`\`\`\"}" \
      "$DISCORD_WEBHOOK_URL" > /dev/null || log "Discord notification failed"
fi

exit $EXIT_CODE
SCRIPT

chmod +x scripts/entrypoint.sh
```

### Build & push

```bash
cd ~/claude-trading-serverless

# ─── Créer le namespace dans le Container Registry ───
SCW_NAMESPACE=$(scw registry namespace create \
    name=claude-trading \
    region=fr-par \
    -o json | jq -r '.id')
echo "Registry Namespace ID: $SCW_NAMESPACE"

# ─── Login Docker vers Scaleway Registry ───
scw registry login

# ─── Build & Push ───
IMAGE="rg.fr-par.scw.cloud/claude-trading/claude-bot:latest"

docker build -t "$IMAGE" .
docker push "$IMAGE"

echo "Image pushed: $IMAGE"
```

---

## PHASE 2 — Secrets (3 min)

Stocker les secrets via Scaleway Secret Manager, référencés ensuite dans les job definitions.

```bash
# ─── Créer les secrets ───
# Chaque secret = 1 valeur. Scaleway Serverless Jobs les injecte
# comme variables d'environnement dans le container.

scw secret secret create \
    name=anthropic-api-key \
    region=fr-par \
    -o json

# Puis créer une version avec la valeur :
scw secret version create \
    secret-id=<SECRET_ID> \
    data="$(echo -n 'sk-ant-api03-XXXXXXXX' | base64)" \
    region=fr-par

# Répéter pour chaque secret :
# - alpaca-api-key
# - alpaca-secret-key
# - discord-webhook-url
# - dappier-api-key
# - scw-s3-access-key
# - scw-s3-secret-key
```

> 💡 **Alternative rapide :** passer les secrets comme `environment-variables`
> directement dans la job definition (moins sécurisé mais plus rapide pour
> le prototyping). On bascule vers Secret Manager une fois validé.

---

## PHASE 3 — Créer les Job Definitions (5 min)

```bash
IMAGE="rg.fr-par.scw.cloud/claude-trading/claude-bot:latest"

# ─── Job 1 : Morning Run ───
MORNING_ID=$(scw jobs definition create \
    name=trading-morning-run \
    cpu-limit=2000 \
    memory-limit=2048 \
    image-uri="$IMAGE" \
    command="/app/scripts/entrypoint.sh" \
    environment-variables.ALPACA_API_KEY="PKXXXXXXXXXXXXXXXX" \
    environment-variables.ALPACA_SECRET_KEY="XXXXXXXXXXXXXXXX" \
    environment-variables.ALPACA_BASE_URL="https://paper-api.alpaca.markets" \
    environment-variables.ANTHROPIC_API_KEY="sk-ant-api03-XXXXXXXX" \
    environment-variables.DAPPIER_API_KEY="dap_XXXXXXXX" \
    environment-variables.DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/XXXXX/XXXXX" \
    environment-variables.SCW_S3_ACCESS_KEY="SCWXXXXXXXXXXXXXXXXX" \
    environment-variables.SCW_S3_SECRET_KEY="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" \
    environment-variables.RUN_TYPE="morning" \
    job-timeout=1800 \
    cron-schedule.schedule="15 15 * * 1-5" \
    cron-schedule.timezone="Europe/Paris" \
    region=fr-par \
    -o json | jq -r '.id')

echo "Morning Job ID: $MORNING_ID"

# ─── Job 2 : Midday Check ───
MIDDAY_ID=$(scw jobs definition create \
    name=trading-midday-check \
    cpu-limit=1000 \
    memory-limit=1024 \
    image-uri="$IMAGE" \
    command="/app/scripts/entrypoint.sh" \
    environment-variables.ALPACA_API_KEY="PKXXXXXXXXXXXXXXXX" \
    environment-variables.ALPACA_SECRET_KEY="XXXXXXXXXXXXXXXX" \
    environment-variables.ALPACA_BASE_URL="https://paper-api.alpaca.markets" \
    environment-variables.ANTHROPIC_API_KEY="sk-ant-api03-XXXXXXXX" \
    environment-variables.DAPPIER_API_KEY="dap_XXXXXXXX" \
    environment-variables.DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/XXXXX/XXXXX" \
    environment-variables.SCW_S3_ACCESS_KEY="SCWXXXXXXXXXXXXXXXXX" \
    environment-variables.SCW_S3_SECRET_KEY="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" \
    environment-variables.RUN_TYPE="midday" \
    job-timeout=900 \
    cron-schedule.schedule="0 18 * * 1-5" \
    cron-schedule.timezone="Europe/Paris" \
    region=fr-par \
    -o json | jq -r '.id')

echo "Midday Job ID: $MIDDAY_ID"

# ─── Job 3 : EOD Review ───
EOD_ID=$(scw jobs definition create \
    name=trading-eod-review \
    cpu-limit=2000 \
    memory-limit=2048 \
    image-uri="$IMAGE" \
    command="/app/scripts/entrypoint.sh" \
    environment-variables.ALPACA_API_KEY="PKXXXXXXXXXXXXXXXX" \
    environment-variables.ALPACA_SECRET_KEY="XXXXXXXXXXXXXXXX" \
    environment-variables.ALPACA_BASE_URL="https://paper-api.alpaca.markets" \
    environment-variables.ANTHROPIC_API_KEY="sk-ant-api03-XXXXXXXX" \
    environment-variables.DAPPIER_API_KEY="dap_XXXXXXXX" \
    environment-variables.DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/XXXXX/XXXXX" \
    environment-variables.SCW_S3_ACCESS_KEY="SCWXXXXXXXXXXXXXXXXX" \
    environment-variables.SCW_S3_SECRET_KEY="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" \
    environment-variables.RUN_TYPE="eod" \
    job-timeout=1200 \
    cron-schedule.schedule="30 21 * * 1-5" \
    cron-schedule.timezone="Europe/Paris" \
    region=fr-par \
    -o json | jq -r '.id')

echo "EOD Job ID: $EOD_ID"

# ─── Sauvegarder les IDs ───
cat > ~/.claude-trading-jobs << EOF
MORNING_ID=$MORNING_ID
MIDDAY_ID=$MIDDAY_ID
EOD_ID=$EOD_ID
EOF

echo "Job IDs saved to ~/.claude-trading-jobs"
```

> 📝 **Note sur `command` vs `args` :** Le champ `command` dans Scaleway
> Serverless Jobs correspond au startup command du container. Le run type
> (`morning`, `midday`, `eod`) est passé via la variable d'environnement
> `RUN_TYPE` plutôt qu'en argument pour simplifier la config.
> L'entrypoint lit `${RUN_TYPE}` si aucun argument n'est passé.

> Adapter `entrypoint.sh` pour lire `$RUN_TYPE` en fallback :
> ```bash
> RUN_TYPE="${1:-${RUN_TYPE:-morning}}"
> ```

---

## PHASE 4 — Vérifier (2 min)

```bash
# Lister les jobs
scw jobs definition list region=fr-par

# Trigger un run manuellement
scw jobs definition start "$MORNING_ID" region=fr-par

# Suivre l'exécution
RUN_ID=$(scw jobs run list region=fr-par -o json | jq -r '.[0].id')
scw jobs run get "$RUN_ID" region=fr-par

# Attendre la fin
scw jobs run wait "$RUN_ID" region=fr-par

# Vérifier le state S3
s3scw s3 ls s3://claude-trading/
s3scw s3 cp s3://claude-trading/progress.md -
```

---

## PHASE 5 — Aliases locaux (monitoring)

```bash
cat >> ~/.bashrc << 'ALIASES'
# ── Claude Trading Bot — Scaleway Serverless ──
source ~/.claude-trading-jobs 2>/dev/null

# Monitoring
alias bot-jobs="scw jobs definition list region=fr-par"
alias bot-runs="scw jobs run list region=fr-par -o json | jq -r '.[] | \"\(.id) \(.state) \(.started_at) \(.job_definition_id)\"' | head -20"
alias bot-pos="aws --profile scaleway s3 cp s3://claude-trading/progress.md -"
alias bot-reports="aws --profile scaleway s3 ls s3://claude-trading/reports/ --recursive | tail -10"
alias bot-logs="aws --profile scaleway s3 ls s3://claude-trading/logs/ --recursive | tail -10"

# Trigger manuel
alias bot-run-morning="scw jobs definition start \$MORNING_ID region=fr-par"
alias bot-run-midday="scw jobs definition start \$MIDDAY_ID region=fr-par"
alias bot-run-eod="scw jobs definition start \$EOD_ID region=fr-par"

# Lire un log spécifique
bot-log() {
    local LOG_KEY="${1:?Usage: bot-log logs/morning-20260309-151500.log}"
    aws --profile scaleway s3 cp "s3://claude-trading/$LOG_KEY" -
}

# Update image (après rebuild)
bot-deploy() {
    local IMAGE="rg.fr-par.scw.cloud/claude-trading/claude-bot:latest"
    docker build -t "$IMAGE" ~/claude-trading-serverless/
    docker push "$IMAGE"
    echo "Image pushed. Les prochains job runs utiliseront la nouvelle image."
}

# Nettoyage logs > 30 jours
bot-cleanup() {
    local CUTOFF=$(date -d '30 days ago' +%Y-%m-%d)
    aws --profile scaleway s3 ls s3://claude-trading/logs/ \
        | awk -v d="$CUTOFF" '$1 < d {print "s3://claude-trading/logs/"$4}' \
        | xargs -r -I{} aws --profile scaleway s3 rm {}
    echo "Old logs cleaned up"
}
ALIASES

source ~/.bashrc
```

---

## PHASE 6 — Upgrade & opérations

### Mettre à jour l'image

```bash
cd ~/claude-trading-serverless
# Modifier Dockerfile, scripts, CLAUDE.md...
bot-deploy
# C'est tout. Le prochain cron utilisera la nouvelle image.
```

### Modifier un cron schedule

```bash
scw jobs definition update "$MORNING_ID" \
    cron-schedule.schedule="30 15 * * 1-5" \
    cron-schedule.timezone="Europe/Paris" \
    region=fr-par
```

### Changer les resources

```bash
# Passer le morning run à 4 vCPU / 4 GB
scw jobs definition update "$MORNING_ID" \
    cpu-limit=4000 \
    memory-limit=4096 \
    region=fr-par
```

### Upgrader Claude en Opus

```bash
# Modifier la ligne dans entrypoint.sh :
#   --model claude-opus-4-6
# Puis :
bot-deploy
```

### Désactiver un job temporairement

```bash
# Supprimer le cron schedule
scw jobs definition update "$MIDDAY_ID" \
    cron-schedule.schedule="" \
    region=fr-par

# Réactiver
scw jobs definition update "$MIDDAY_ID" \
    cron-schedule.schedule="0 18 * * 1-5" \
    cron-schedule.timezone="Europe/Paris" \
    region=fr-par
```

### Consulter les logs Cockpit

```bash
# Cockpit (observabilité intégrée) dispo dans la console Scaleway
# → https://console.scaleway.com/cockpit
# Les logs stdout/stderr des job runs y sont automatiquement
# Les metrics CPU/RAM aussi
```

---

## Coûts mensuels

| Poste | Montant |
|-------|---------|
| Scaleway Serverless Jobs | **~0€** (free tier) |
| Scaleway Container Registry | Gratuit (75 GB inclus) |
| Scaleway Object Storage | ~0,01€ (quelques MB de logs/reports) |
| API Claude Sonnet 4.6 (~60 runs/mois) | ~$8-15 |
| Alpaca paper trading | Gratuit |
| **Total** | **~$8-15/mois (API only)** |

**vs playbook VPS :** économie de 6,42€/mois d'infra → **~77€/an**.

> Pour Opus sur le morning run : changer `--model claude-opus-4-6` → +$10-15/mois.

---

## Quick ref — Commandes du quotidien

```bash
# ── Monitoring ──
bot-jobs                          # Lister les job definitions
bot-runs                          # Derniers job runs (état, date)
bot-pos                           # Lire progress.md depuis S3
bot-reports                       # Lister les rapports
bot-logs                          # Lister les logs
bot-log logs/morning-XXX.log      # Lire un log spécifique

# ── Triggers manuels ──
bot-run-morning                   # Lancer le morning run maintenant
bot-run-midday                    # Lancer le midday check
bot-run-eod                       # Lancer l'EOD review

# ── Opérations ──
bot-deploy                        # Rebuild + push image
bot-cleanup                       # Supprimer les logs > 30 jours

# ── Scaleway raw ──
scw jobs definition list region=fr-par
scw jobs run list region=fr-par
scw jobs definition start <JOB_ID> region=fr-par
scw jobs run get <RUN_ID> region=fr-par
scw jobs run stop <RUN_ID> region=fr-par
```

---

## Troubleshooting

### Le job run échoue immédiatement

```bash
# Vérifier le status et le reason
scw jobs run get <RUN_ID> region=fr-par -o json | jq '{state, error_message}'

# Causes fréquentes :
# - Image not found → vérifier scw registry login + image URI
# - OOM → augmenter memory-limit
# - Timeout → augmenter job-timeout
```

### Claude Code ne trouve pas les MCP servers

```bash
# Les MCP servers npx ont besoin de network access
# Serverless Jobs a un accès internet sortant par défaut ✅
# Vérifier que ALPACA_API_KEY et autres sont bien set
scw jobs definition get <JOB_ID> region=fr-par -o json | jq '.environment_variables'
```

### Pas de notification Discord

```bash
# Le webhook URL doit être complet (https://discord.com/api/webhooks/...)
# Tester manuellement :
curl -H "Content-Type: application/json" \
  -d '{"content": "test"}' \
  "https://discord.com/api/webhooks/XXXXX/XXXXX"
```

### S3 sync échoue

```bash
# Vérifier que les clés S3 sont bien distinctes de la clé API Scaleway
# L'Object Storage utilise ses propres credentials
# → Console > Object Storage > API Keys
```

---

## Migration depuis le playbook VPS

Si tu as déjà un VPS qui tourne avec l'ancien playbook :

```bash
# 1. Récupérer l'état actuel
scp claude-agent:~/claude-trading/progress.md ./
scp -r claude-agent:~/claude-trading/reports/ ./reports/

# 2. Upload vers S3
s3scw s3 cp progress.md s3://claude-trading/
s3scw s3 sync reports/ s3://claude-trading/reports/

# 3. Déployer les Serverless Jobs (Phase 1-3 ci-dessus)

# 4. Valider avec un run manuel
bot-run-morning

# 5. Une fois validé, supprimer le VPS
scw instance server terminate <SERVER_ID> zone=fr-par-1 with-ip=true with-block=local
```

---

*Playbook Scaleway Serverless Jobs — v1.0 — 9 mars 2026*
*Coût infra : ~0€. Coût API : tu décides.*
