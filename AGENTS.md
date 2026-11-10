# Crypto Market Monitor contributor instructions

- Read AGENTS.md, README.md, ROADMAP.md, DEVLOG.md and relevant module docs before development.
- Python 3.12+, src layout, typed public interfaces, immutable normalized models.
- Official locally owned public REST/WS only; no CCXT, private exchange APIs, keys or orders.
- Deterministic Python monitoring rules; no LLM in the runtime decision path.
- Use Decimal for money and UTC epoch milliseconds for source/receipt time.
- Preserve existing thresholds, configuration fingerprints, event IDs and SQLite state compatibility.
- Reject stale, future, malformed, non-finite and inconsistent data. Never restamp source times.
- Telegram is explicit opt-in; keep credentials and the bound Chat ID private and isolated.
- Read-only commands only; one polling owner. Never print or commit credentials.
- No production stop, deployment, DB migration or cutover without explicit user approval
  after a concrete staged result is reviewable. Local migration does not authorize deployment.
- Run focused offline tests, then python -m pytest, python -m ruff check .,
  python -m ruff format --check . and scripts/check-repository.py.
- Use --once for explicit bounded public network checks; report actual failures.
- Update DEVLOG.md and ROADMAP.md with commands, outcomes, limitations and next acceptance.
- Commit only task files after checks pass. The authorized public repository is
  Issac0797/crypto-market-monitor; do not create or push to another remote without explicit
  authorization. Preserve unrelated user changes.
- Never commit local data, logs, venvs, release archives, secrets or encrypted credentials.
