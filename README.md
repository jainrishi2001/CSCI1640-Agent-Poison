# agent-sec

A sandboxed testbed for LLM agent security. A target agent calls simulated MCP tools. Attacks are poisoned tool descriptions and multi-turn social engineering. Tool calls and outgoing messages go through a policy gateway where defenses plug in. Nothing here touches a real system or sends a real message.

## Prerequisites

- [uv](https://docs.astral.sh/uv/)
- The [gcloud CLI](https://cloud.google.com/sdk/docs/install)
- Application Default Credentials: `gcloud auth application-default login`
- The Vertex AI API enabled on the Google Cloud project named in `.env`

Offline tests do not need cloud credentials. `make smoke` does.

## Quickstart

```bash
make setup
cp .env.example .env
# Fill in VERTEXAI_PROJECT and the three model ids. Do not hardcode models in code.
make test
make smoke
```

Model ids are LiteLLM strings such as `vertex_ai/<model-name>`. `GEMINI_API_KEY` is optional and is only for free-tier debugging with the `gemini/` prefix.

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
  tests/
  analysis/
  paper/game.md
  runs/                  gitignored JSONL output
```

Signing, capability tokens, the settings and LLM wrappers, the run logger, and the benign MCP server are implemented. Agents, the gateway policy loop, the monitor, poisoned tools, and the experiment runner are stubs.
