FROM node:22-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    curl jq git ca-certificates awscli \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Dedicated user
RUN useradd -m -s /bin/bash trader

# ── Python venv with trading deps ──
RUN python3 -m venv /app/.venv
RUN /app/.venv/bin/pip install --no-cache-dir \
    "alpaca-py>=0.30.0" \
    "python-dotenv>=1.0.0" \
    "pandas>=2.0.0" \
    "numpy>=1.24.0"

# ── App files ──
COPY --chown=trader:trader trading/ /app/trading/
COPY --chown=trader:trader CLAUDE.md /app/CLAUDE.md
COPY --chown=trader:trader deploy/config/.mcp.json /app/.mcp.json
COPY --chown=trader:trader deploy/config/settings.json /home/trader/.claude/settings.json
COPY --chown=trader:trader .claude/agents/ /app/.claude/agents/
COPY --chown=trader:trader .claude/commands/ /app/.claude/commands/
COPY --chown=trader:trader .claude/hooks/ /app/.claude/hooks/
COPY --chown=trader:trader deploy/scripts/entrypoint.sh /app/entrypoint.sh

# ── Plugins (equity-research, financial-analysis) ──
COPY --chown=trader:trader plugins/ /app/plugins/

# Ensure hooks are executable and dirs exist
RUN chmod +x /app/entrypoint.sh \
    && mkdir -p /app/reports /app/logs /home/trader/.claude \
    && chown -R trader:trader /app /home/trader

USER trader
WORKDIR /app

# Make venv available
ENV PATH="/app/.venv/bin:$PATH"
ENV VIRTUAL_ENV="/app/.venv"

ENTRYPOINT ["/app/entrypoint.sh"]
