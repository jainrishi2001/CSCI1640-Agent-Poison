# Roadmap

Milestones match the GitHub milestones. Check items off as their issues close.

## M1 · Foundation ✅
- [x] Repo scaffold (uv, tests, lint, Makefile)
- [x] LLM wrapper with free-tier rate limits, daily quota, retries
- [x] Target agent loop, secret store, pass-through policy gateway
- [x] Signing and capability-token primitives
- [x] Live end-to-end demo on Gemma 4 31B (`make demo`)

## M2 · Door 1: tool-poisoning baseline
- [x] Door-1 attack cases (30)
- [ ] Benign server with realistic fake data
- [x] Poisoned tool server + rug pull
- [ ] Judge v1
- [ ] Door-1 runner (resume, errors, concurrency)
- [ ] Pilot + no-defense baseline results

## M3 · Door 2 and chained attacks
- [ ] Target role, disclosure policy, and pretext personas
- [ ] Attacker agent + conversation loop
- [ ] Chained scenarios
- [ ] Judge v2 (conversation leaks, turns to leak)
- [ ] Door-2 and chained baseline results

## M4 · Defenses
- [ ] D1 hardened prompt
- [ ] D2 signed, pinned manifests
- [ ] D3 capability-token enforcement
- [ ] D4 monitor agent
- [ ] Defense wiring in the runner + full grid

## M5 · Results and paper
- [ ] Analysis notebook (tables, CIs, charts)
- [ ] Security game write-up
- [ ] Final paper

## Stretch
- [ ] Adaptive attacker
- [ ] Second target model (Gemini 3.5 Flash Lite)
- [ ] LLM description vetting or CaMeL-lite isolation

## Suggested split for two people

The two tracks touch different files, so they can proceed in parallel after M2's interfaces exist.

| Track A: attacks and measurement | Track B: defenses and theory |
|---|---|
| Poisoned server, runner, judge (M2) | Door-1 attack cases (M2, YAML only) |
| Attacker agent, conversation loop, judge v2 (M3) | D2 and D3 defenses (M4, can start now) |
| Full grid runs, analysis (M4–M5) | D1, D4, security game write-up (M4–M5) |
| | Pretext personas and chained scenarios (M3, YAML) |
