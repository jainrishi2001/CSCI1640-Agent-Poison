# Decision log

Short records of decisions that shape the project. Newest first. Add an entry whenever you make a choice a teammate would otherwise have to rediscover.

Format: **date: decision.** Why. Consequences.

---

**2026-11-17: Pin `SYSTEM_AS_USER=false`.** A live run showed Gemma 4 31B accepts the system role (`system_role_mode = system`). Pinning it means every run uses the same instruction placement, which matters because placement affects injection resistance. Report it in the paper.

**2026-11-17: Write our own door-1 cases in MCPTox's three styles instead of depending on the MCPTox dataset.** The dataset is only available through an anonymized link that may disappear, and its cases target real servers we don't run. We cite MCPTox for the design (P1/P2/P3 plus rug pulls).

**2026-11-17: Patient retries for provider overload.** The free tier returns 503 "high demand" errors. Retries back off from 10 s up to 60 s with jitter (about 3 to 4 minutes total) so long runs don't crash. The runner will additionally record failed runs as `errored` and resume.

**2026-11-17: Models: Gemma 4 31B target, Gemma 4 26B attacker, Gemini 3.1 Flash Lite monitor.** On the free tier, Flash models allow only 20 requests/day, Flash Lite 500/day, and Gemma 4 14,400/day. Gemma is the only option with enough volume for the target and attacker. Gemma 4 31B passed a tool-calling smoke test. Limitation: attacker and target are the same family at different sizes; a Flash Lite second target (stretch) addresses this.

**2026-11-17: Use the Google AI Studio Gemini API free tier, not Vertex AI.** From November 16, 2026, Free Trial credits no longer cover Gemini on Vertex, and upgrading would bill real money. The AI Studio key lives in a project with no billing account, so it cannot be charged. Trade-offs: daily limits, 16K tokens/minute for Gemma, ~25–30 s latency, and Google may use free-tier inputs (acceptable: all data is synthetic).

**2026-11-17: Scope reduced after instructor feedback.** Core: 4 defenses (hardened prompt, signed manifests, capability tokens, monitor), a fixed attacker, and a one-page security game. Stretch: adaptive attacker, CaMeL-style isolation, LLM description vetting, second target model.

**2026-11-17: uv, not Docker.** Nothing heavy runs locally; models are remote. uv gives a locked, reproducible environment with less friction. A Dockerfile can be added later if graders need it.

**2026-11-17: Write our own capability tokens instead of using a macaroon library.** About 100 lines of HMAC-SHA256 chaining is easy to audit and to describe in the formal section. Supports `field = value` and `field in a,b` caveats; `mint_for_task` never issues an unattenuated token.

**2026-11-17: All policy decisions go through one gateway.** Defenses are plug-ins on a single checkpoint for tool listings, tool calls, and outgoing messages, so adding a defense never requires changing the agent, and every decision lands in the run log.
