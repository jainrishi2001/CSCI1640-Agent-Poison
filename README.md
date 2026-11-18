# agent-sec

A sandboxed testbed for LLM agent security. A target agent calls simulated MCP tools. Attacks are poisoned tool descriptions and multi-turn social engineering. Tool calls and outgoing messages go through a policy gateway where defenses plug in. Nothing here touches a real system or sends a real message.

## What this project is

We attack an AI agent through two doors, **poisoned tool descriptions** and **social-engineering conversations** (plus a chained attack using both), and measure which defenses hold: a hardened prompt, signed tool manifests, capability tokens, and a monitor agent.

- **Start with [`docs/`](docs/README.md):** [solution architecture](docs/architecture/solution.md) (what and why), [technical architecture](docs/architecture/technical.md) (how), [roadmap](docs/roadmap.md), [decision log](docs/decision-log.md).
- **Work is tracked as GitHub issues** generated from [`docs/backlog.yaml`](docs/backlog.yaml).

## Prerequisites

- [uv](https://docs.astral.sh/uv/)
- A **Google AI Studio API key** on the free tier (aistudio.google.com, "Get API key", in a project with **no billing account**). No Google Cloud billing or gcloud login is needed.
- Optional: the GitHub CLI (`gh`) to create issues from the backlog.

Offline tests need no key. `make smoke` and `make demo` call the real model.

## Quickstart

```bash
make setup
cp .env.example .env
# Set GEMINI_API_KEY and keep the model ids from .env.example. Add SYSTEM_AS_USER=false.
make test     # offline, free
make smoke    # 1 model call: checks tool calling
make demo     # ~3 model calls: one real task end to end
```

Models (free tier): target `gemini/gemma-4-31b-it`, attacker `gemini/gemma-4-26b-a4b-it`, monitor `gemini/gemini-3.1-flash-lite`. Limits live in `configs/limits.yaml`; the code waits and retries automatically and stops cleanly at the daily quota. Never hardcode model ids.

## Layout

```
agent-sec/
  pyproject.toml
  uv.lock
  .python-version
  .env.example
  .gitignore
  Makefile
  README.md
  src/agent_sec/
    __init__.py
    config.py            settings from .env
    llm.py               LiteLLM chat wrapper
    logging_utils.py     append-only JSONL run logger
    servers/
      benign.py          simulated read_file, send_email, lookup_employee, fetch_url
      secret_store.py    planted secret behind get_access_code
      poisoned.py        poisoned descriptions and a rug-pull switch
    agent/
      target.py          MCP client and tool-calling loop, via the gateway
      attacker.py        persona LLM, up to N turns
    gateway/
      gateway.py         allow or deny tool calls and outgoing messages
      signing.py         Ed25519 manifests and SHA-256 pins
      capabilities.py    HMAC capability tokens
      monitor.py         monitor LLM with veto
      prompts.py         hardened system prompt (defense D1)
    eval/
      judge.py           leak and unauthorized-tool labels
      runner.py          attack x defense x seed grid, JSONL under runs/
  attacks/
    mcptox_subset/       raw dumps stay gitignored
    pretexts.yaml        IT support, manager, and vendor personas
    chained.yaml         poisoned description, then a conversation
  configs/
    default.yaml         placeholder experiment grid
  scripts/
    smoke_llm.py         one dummy tool call against TARGET_MODEL
    demo_task.py         one real task end to end
    create_issues.py     GitHub issues from docs/backlog.yaml
  tests/
  analysis/
  paper/game.md
  docs/                architecture, roadmap, decisions, backlog, Cursor prompts
  runs/                  gitignored JSONL output
```

## Status

Built and tested: LLM wrapper with free-tier limits, target agent loop, policy gateway, secret store, benign tools, signing and capability-token primitives. Next: tool-poisoning attack and baseline (milestone M2). See [docs/roadmap.md](docs/roadmap.md).
