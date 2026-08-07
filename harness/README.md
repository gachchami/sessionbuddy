# Sessionbuddy private test harness

This directory contains local test, accessibility, fixture, and seed tooling. It is intentionally ignored by the project `.gitignore`.

## Python setup

```bash
cd harness
uv sync
uv run pytest
uv run python seed.py --output .local/seed.json
```

The seed command is deterministic by default. Use `--seed` to generate a different repeatable dataset.

## Browser and accessibility setup

```bash
cd harness
npm install
npx playwright install chromium
SESSIONBUDDY_BASE_URL=http://127.0.0.1:3000 npm test
```

The Playwright suite skips when `SESSIONBUDDY_BASE_URL` is unset. Copy `.env.example` to `.env` when local defaults are useful.

## Layout

- `tests/`: pytest tests and reusable fixture factories
- `e2e/`: Playwright browser and Axe accessibility checks
- `seed.py`: deterministic JSON seed-data generator
- `.local/`: generated private artifacts; never commit this directory

