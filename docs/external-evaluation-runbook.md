# External SessionBoard evaluation

Use `scripts/run_sbek.sh` instead of rediscovering the evaluator checkout or
reconstructing its Docker command.

The launcher remembers these non-secret integration facts:

- deployed target: `https://sessionbuddy-development.shiny-cloud-dd47.workers.dev`
- eval checkout discovery: `/private/tmp/sessionbuddy-evals.*/repo`
- Claude OAuth Docker volume: `sessionbuddy-claude-auth`
- browser image: `mcr.microsoft.com/playwright:v1.62.1-noble`
- browser location inside that image: `/ms-playwright`
- required saved personas: `organizer` and `speaker` in the eval checkout's
  ignored `.auth/` directory

It never reads or prints cookies, OAuth data, or application secrets.

Before a paid run, the launcher makes a safe request with each saved persona
and stops immediately if either session has expired. Refresh an expired state
with:

```sh
scripts/run_sbek.sh auth organizer
scripts/run_sbek.sh auth speaker
```

Each command waits for the matching one-time link. The browser state remains in
the eval kit's ignored `.auth/` directory.

When one application account genuinely holds both roles, reuse the validated
browser state without another email round-trip:

```sh
scripts/run_sbek.sh auth speaker --reuse organizer
```

Inspect the resolved setup without launching an evaluation:

```sh
scripts/run_sbek.sh where
```

Run the required areas:

```sh
scripts/run_sbek.sh \
  --areas call-for-papers,abstract-management,speaker-management,content-management,ai-agenda,public-widgets \
  --agent-model claude-sonnet-5 \
  --judge-model claude-opus-5
```

Resume an interrupted run without paying for completed scenarios again:

```sh
scripts/run_sbek.sh resume runs/<timestamp> \
  --agent-model claude-sonnet-5 \
  --judge-model claude-opus-5
```

Override a discovered value only when the local setup changes:

```sh
SBEK_ROOT=/path/to/killmysaas-evals \
SBEK_CLAUDE_AUTH_VOLUME=sessionbuddy-claude-auth \
SBEK_TARGET_URL=https://example.workers.dev \
scripts/run_sbek.sh where
```

The Playwright image and the eval kit's Playwright package must have the same
version. The current pinned pair is 1.62.1. A missing browser executable means
the launcher was bypassed or `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` was not
passed to the container.
