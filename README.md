# agent-sec

A sandboxed testbed for LLM agent security. A target agent calls simulated MCP tools. Attacks are poisoned tool descriptions and multi-turn social engineering. Tool calls and outgoing messages go through a policy gateway where defenses plug in. Nothing here touches a real system or sends a real message.

## What this project is

We attack an AI agent through two doors, **poisoned tool descriptions** and **social-engineering conversations** (plus a chained attack using both), and measure which defenses hold: a hardened prompt, signed tool manifests, capability tokens, and a monitor agent.

- **Start with [`docs/`](docs/README.md):** [solution architecture](docs/architecture/solution.md) (what and why), [technical architecture](docs/architecture/technical.md) (how), [roadmap](docs/roadmap.md), [decision log](docs/decision-log.md).

![Solution architecture: attacks, sandboxed testbed with the policy gateway and defenses, measurement pipeline, and project plan](docs/architecture/solution-architecture.png)

## Prerequisites

- [uv](https://docs.astral.sh/uv/)
- One of two model providers (pick per role; see `.env.example`):
  - **Google AI Studio, free tier (default):** an API key from aistudio.google.com ("Get API key"), in a project with **no billing account**. No Google Cloud billing or gcloud login is needed. Costs $0.
  - **Vertex AI (optional):** a GCP project with the Vertex AI API enabled and `gcloud auth application-default login`. Calls are **billed**. Current models are served only from the `global` location, and the Gemma 4 model offered there is 26B, not 31B.

Offline tests need no key. `make smoke` and `make demo` call the real model.

## Quickstart

```bash
make setup
cp .env.example .env
# Option A: set GEMINI_API_KEY. Option B: uncomment the Vertex block and set VERTEXAI_PROJECT.
make test     # offline, free
make smoke    # 1 model call: checks tool calling
make demo     # ~3 model calls: one real task end to end
```

Models, AI Studio (default): target `gemini/gemma-4-31b-it`, attacker `gemini/gemma-4-26b-a4b-it`, monitor `gemini/gemini-3.1-flash-lite`. Vertex alternative: target `vertex_ai/gemini/gemma-4-26b-a4b-it-maas`, attacker and monitor `vertex_ai/gemini-3.1-flash-lite`. The id prefix picks the provider, and roles can mix providers, so results can be compared across models. Limits live in `configs/limits.yaml` (free-tier limits, plus self-imposed caps on the billed Vertex models); the code waits and retries automatically and stops cleanly at the daily quota. Never hardcode model ids.

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
  tests/
  analysis/
  paper/game.md
  docs/                architecture, roadmap, decisions
  runs/                  gitignored JSONL output
```

## Status

Built and tested: LLM wrapper with free-tier limits, target agent loop, policy gateway, secret store, benign tools, signing and capability-token primitives. Next: tool-poisoning attack and baseline (milestone M2). See [docs/roadmap.md](docs/roadmap.md).
