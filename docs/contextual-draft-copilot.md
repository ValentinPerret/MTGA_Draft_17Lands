# Contextual Draft Copilot

## Purpose and safety boundary

The copilot is a read-only sidecar for MTG Arena drafts. It parses Arena's local
`Player.log`, joins Arena card IDs to locally cached statistics, computes a
deterministic recommendation, and renders it in the existing desktop UI. It never
sends clicks, keyboard input, deck submissions, or pick commands to Arena.

The `legacy` advisor remains available and is the default until the contextual engine
has been replayed against a complete detailed-log draft on the target machine. Select
`contextual_v2` in **File -> Preferences** or with the Mini Mode `V1`/`V2` control.

## Architecture

The controller takes one locked scanner snapshot and passes normalized state through
`AdvisorService`. The legacy path is unchanged. The contextual path composes:

- `statistics.py`: sample-aware shrinkage, signed improvement-when-drawn handling,
  format/archetype priors, and missing-data caveats.
- `archetypes.py`: probabilities for the ten color pairs, mono-color lanes, and
  fixing-supported splashes.
- `deck_search.py` and `deck_quality.py`: bounded beam search over plausible final
  40-card shells, including 22–24 nonlands, curve, interaction, mana, and replacement.
- `synergy.py` and `rulepacks/innistrad_planar.py`: generic role edges plus explicit
  nonlinear packages for Humans, Zombies, Madness, Reanimator, Spider Spawning,
  Spells, Sacrifice, and Vampires.
- `signals.py`: discounted open-lane and wheel evidence that cannot overpower deck fit.
- `confidence.py` and `explanations.py`: evidence-based confidence, concise reasons,
  future needs, and data caveats.
- `model_router.py` and `codex_reviewer.py`: optional second-pass routing and the
  user-approved local Codex bridge.

Every recommendation preserves deterministic component scores even if Codex later
changes the displayed ordering.

## Local Codex reviewer

This personal-machine integration uses the Codex executable bundled with the ChatGPT
desktop app (or a healthy `codex` command on `PATH`). Codex reuses its own saved
ChatGPT sign-in. The draft tool neither opens nor parses Codex credential files and
does not accept a ChatGPT password, cookie, access token, or API key.

The bridge is deliberately constrained:

- Opt-in and disabled by default.
- Automatic review only for close, uncertain, pivot, splash, or package-sensitive
  decisions; decisive local results are skipped.
- A configurable automatic-call limit, defaulting to 10 per draft.
- One cached result—including failures—per equivalent state.
- Ephemeral Codex sessions, read-only sandbox, `approval_policy="never"`, no shell,
  web search, apps, browser, computer use, image generation, skills, or repo rules.
- A temporary empty working directory and a sanitized process environment.
- Strict timeout and a worker-thread boundary; Tk is updated only by the main loop.
- Strict output schema and validation that the selected Arena card ID is in the pack.
- Lower-confidence disagreements cannot replace the local recommendation.

The minimized payload contains only format name, pack/pick numbers, cards in the
current pack, a pool summary, candidate scores/deck-fit summaries, lane probabilities,
and relevant statistics. It excludes full logs, account/draft identifiers, timestamps,
filesystem paths, tokens, and unrelated history.

This is an experimental convenience for the named local machine, not a general API
or authentication pattern for redistributed applications. A broadly distributed
version should use a separately supported backend and explicit billing/auth design.

## Configuration

The persisted section is:

```json
{
  "model_assistance": {
    "enabled": false,
    "provider": "codex",
    "model": "",
    "automatic_call_limit_per_draft": 10,
    "close_score_margin": 4.0,
    "skip_score_margin": 8.0,
    "skip_confidence": 0.85,
    "minimum_remaining_pick_seconds": 20,
    "request_timeout_seconds": 12.0,
    "allow_manual_analysis": true
  }
}
```

Leave `model` blank to use Codex's current recommended default. The UI exposes the
opt-in, optional model override, and per-draft automatic limit. Advanced thresholds
can be changed in the platform configuration file documented in the README.

## Running and building

```bash
poetry install
poetry run python main.py
poetry run pytest tests/
poetry run pyinstaller main.spec --clean
```

On macOS, use a Python build that includes Tk. The packaged application locates
`/Applications/ChatGPT.app/Contents/Resources/codex` at runtime; Codex is intentionally
not copied into the application bundle.

## Arena setup and recovery

Enable **Arena -> Options -> Account -> Detailed Logs (Plugin Support)** and restart
Arena. Current Arena versions can expose `Draft.Notify` pack states even when detailed
logs are disabled, but without pick confirmations the complete pool cannot be trusted.
The overlay shows this as incomplete state and suppresses Codex calls.

Use **Pause Monitoring**, **Resync from Player.log**, and **V1/V2** from Mini Mode when
recovering from a stale state. Resync never makes an Arena action.

## Performance and reliability

The deterministic pack evaluation is bounded and covered by a sub-one-second latency
test. Codex review runs after that result is available and cannot block the UI. The
coordinator records only safe metadata: state digest prefix, routing reason, latency,
status, and whether validated review changed the ordering.

The monolithic upstream suite originally exposed a pre-existing Tk crash in
`dashboard_recap.fetch_17lands_record`, where a worker thread invoked Tk directly.
This branch repairs that boundary with a queue polled by Tk's owning thread.

On the implementation machine, the final monolithic test run completed with 711
passed and 1 skipped in 83.92 seconds. A ten-iteration benchmark reported:

- Sanitized draft replay parsing: 3.37 ms median, 3.65 ms p95.
- Contextual evaluation of a 15-card pack with a 30-card pool: 423.94 ms median,
  433.90 ms p95.
- Minimized Codex-review state construction: 0.12 ms median, 0.13 ms p95.

Re-run with `poetry run python Tools/benchmark_contextual_copilot.py`.

## Known limitations

- P1P1 has no pool evidence, so flexibility and raw/sample-adjusted power dominate.
- Missing or stale detailed-log data prevents trustworthy pool reconstruction and
  suppresses model review.
- Cube statistics must match the exact module and permitted date window.
- Cards with missing Arena IDs cannot be submitted for Codex review.
- A Codex timeout, authentication problem, usage limit, unavailable binary, malformed
  response, or lower-confidence disagreement leaves the local ranking unchanged.
- The reviewer is advisory and coding-agent latency/availability are not guaranteed
  for a timed draft product.

## 17Lands attribution and compliance

Statistics are attributed to [17Lands](https://www.17lands.com/). Current published
guidance requires visible attribution, discourages automated API scraping, requests a
12-day embargo for normal sets and a 7-day embargo for short specialty/Cube sets, and
normally licenses public bulk datasets under CC BY 4.0. This branch does not commit or
redistribute raw datasets. It preserves local caching and the upstream data path.

Before any public distribution, confirm that the upstream generated-data workflow and
the exact current Cube module/date window still meet the latest
[usage guidelines](https://www.17lands.com/usage_guidelines),
[terms](https://www.17lands.com/terms_of_service), and
[public-dataset terms](https://api.17lands.com/public_datasets).

## Default-readiness recommendation

Keep `legacy` as the default for now. The contextual engine's deterministic behavior,
latency, rule packs, and fallbacks are tested, but promotion should wait for at least
one complete live draft after Detailed Logs are enabled, plus a review of real pick
reconstruction and dataset-module matching on this machine.
