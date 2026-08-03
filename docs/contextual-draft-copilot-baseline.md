# Contextual Draft Copilot Baseline

Recorded on 2026-07-31 before feature work.

## Machine and repository

- Host: macOS 26.5.2 (Darwin 25.5.0), Apple Silicon (`arm64`).
- Base repository: `unrealities/MTGA_Draft_17Lands`.
- Writable fork: `ValentinPerret/MTGA_Draft_17Lands`.
- Base revision: `1e78f95`, tag `MTGA_Draft_Tool_V0419`, application version 4.19.
- Feature branch: `feature/contextual-draft-copilot`.
- The checkout was clean before this document was added.

## Existing architecture

The existing application already supplies the required sidecar boundary: it reads
Arena's `Player.log`, resolves local Arena card IDs, persists active draft state,
loads locally cached statistics, and renders recommendations in a Tk/ttkbootstrap
desktop overlay. The legacy `DraftAdvisor`, deck builder, mana-source analyzer,
signals model, and overlay should be extended through a normalized advisor service,
not replaced.

## Baseline tests

The locked dependencies install successfully under Python 3.12.13. The system
Homebrew Python lacks `_tkinter`, so the tests use the bundled Python 3.12 runtime
with Tk 9.0.

`pytest tests/` collected 678 tests. It reached 21% with all completed tests passing
(one expected skip) before the interpreter crashed in the untouched code. The crash
is caused by `dashboard_recap.fetch_17lands_record` calling Tk's `after` method from
a background thread while the main test thread is updating Tk. This is an upstream
baseline failure and must not be hidden when evaluating the feature branch.

## Live Arena ingestion

- Current log: `~/Library/Logs/Wizards Of The Coast/MTGA/Player.log`.
- Previous log: `~/Library/Logs/Wizards Of The Coast/MTGA/Player-prev.log`.
- Both logs explicitly report `DETAILED LOGS: DISABLED`.
- The previous log contains an Innistrad Planar Cube session with all 45
  `Draft.Notify` pack payloads, including P1P1, but no
  `Event_PlayerDraftMakePick`, `GrpId`, or `GrpIds` payloads.

The pack sequence can be reconstructed, but the user's picks cannot be uniquely
recovered without pick confirmations. Live end-to-end validation therefore requires
enabling **MTG Arena > Options > Account > Detailed Logs (Plugin Support)** and
restarting Arena. No paid event should be entered solely for testing.

## 17Lands data and compliance

Current primary guidance was reviewed at:

- <https://www.17lands.com/usage_guidelines>
- <https://www.17lands.com/terms_of_service>
- <https://api.17lands.com/public_datasets>

The current project downloads derived card statistics from the upstream project's
GitHub Pages manifest and retains a manual direct-API fallback. 17Lands requires
visible attribution, discourages automated API scraping, requests a 12-day embargo
for normal expansions and a 7-day embargo for short specialty sets such as Cube,
and prefers its CC BY 4.0 public datasets for bulk analysis. This branch must not
commit or redistribute raw 17Lands datasets. It should preserve local caching,
surface data age/sample caveats, prefer the upstream/local user-initiated path, and
document that direct API access is unsupported and may change.

## Minimal path to a working contextual recommendation

1. Extend the stable recommendation schema with confidence, lane probabilities,
   marginal replacement, score components, and caveats while retaining all legacy
   fields.
2. Add a configuration-gated advisor service with `legacy` and `contextual_v2`.
3. Build deterministic, bounded modules for sample-aware statistics, probabilistic
   lanes, synergy roles, feasible deck shells, marginal deck quality, confidence,
   and explanations.
4. Route the controller and existing overlay through the service; keep legacy as
   the default until a detailed-log live replay succeeds.
5. Add behavioral/golden tests and a full-pack latency benchmark before packaging.
