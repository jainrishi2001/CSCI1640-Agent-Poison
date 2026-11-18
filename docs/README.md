# Project docs

Start here if you're new to the project.

| Doc | Read it for |
|---|---|
| [architecture/solution.md](architecture/solution.md) | **What** we're building and **why**: problem, threat model, attacks, defenses, experiment design, models, hypotheses |
| [architecture/technical.md](architecture/technical.md) | **How** the code works: components, run lifecycle, defense interface, rate limiting, log format, testing |
| [roadmap.md](roadmap.md) | Milestones and what's done |
| [decision-log.md](decision-log.md) | Why things are the way they are |
| [implementation-notes.md](implementation-notes.md) | Low-level build notes: library quirks, exact formats, edge cases |
| [backlog.yaml](backlog.yaml) | Source of truth for GitHub issues |
| [prompts/](prompts/) | Cursor prompts used to build each component |

## Keeping docs current

Docs are only useful if they match the code. The rules:

1. **A PR that changes behavior updates the docs in the same PR.** The PR template has a checkbox for this.
2. **Status markers** (✅ 🟡 ⬜) in `technical.md` are updated when a component lands.
3. **Decisions** a teammate would need to know go in `decision-log.md`, newest first, two to four lines each.
4. **New work** goes into `backlog.yaml` first, then `uv run python scripts/create_issues.py` creates only the new issues.
5. **Roadmap checkboxes** are ticked when the matching issue closes.
6. **Implementation notes** (how something was built, quirks found) go in `implementation-notes.md`, not the gitignored `decisions.md`. When giving Cursor a prompt, add: "Append implementation notes to docs/implementation-notes.md."

## Creating GitHub issues from the backlog

Requires the GitHub CLI (`brew install gh`, then `gh auth login`).

```bash
uv run python scripts/create_issues.py --dry-run   # preview
uv run python scripts/create_issues.py             # create labels, milestones, and missing issues
```

The script is idempotent: it skips labels, milestones, and issues (matched by title) that already exist.
