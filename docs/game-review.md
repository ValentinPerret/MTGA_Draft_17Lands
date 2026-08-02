# Post-Match Game Review

The Game Review module turns MTG Arena's detailed gameplay events into a private,
post-match coaching workflow for Limited games.

## Workflow

1. Enable **Detailed Logs (Plugin Support)** in Arena and restart Arena.
2. Finish a Premier Draft, Quick Draft, Traditional Draft, Sealed, or Cube game.
3. Open **Game Review** and click **Scan Player.log**.
4. Select a completed game to inspect the conservative review and action timeline.
5. Open **Deck Changes** to see whether the submitted list should change.
6. Optionally click **Analyze Game + Deck** for a deeper local review.
7. Use **Progress** to track recurring categories and the finding rate across reviewed
   games.

Scanning and Codex analysis both run on background threads. The status line explains
the active operation; a Codex review can take up to two minutes.

## Evidence model

The parser reconstructs only state Arena recorded: the player's visible hand, both
battlefields, life totals, legal action requests, chosen responses, turn/phase state,
game result, and the exact submitted main deck and sideboard. Local card datasets
provide names, oracle text, mana value, color, and available 17Lands context for
recorded Arena card identifiers.

The immediate review intentionally makes few claims. It currently detects unusually
risky opening-hand keeps, possible missed land drops, and timeout losses. A deeper
Codex review compares the recorded legal choices and board snapshots for sequencing,
combat, interaction, and resource-use opportunities. Every finding includes a cited
turn, evidence, an alternative line, a reusable practice tip, certainty, and
confidence.

### Deck-change evidence

The exact submitted main deck is reduced to a one-way deck fingerprint so games with
the same list can be compared. The immediate analyzer can flag structural issues such
as playing more than 40 cards or an unusually low land count. It waits for repeated
same-deck evidence before suggesting changes for mana screw, flood, or cards that
remain stranded in hand. Additions must be present in the recorded sideboard, except
for basic lands that are freely available in Limited.

The Codex pass receives the current submitted list, sideboard, and up to eight bounded
same-deck summaries. It is explicitly instructed—and its output is validated—not to
recommend a cut merely because the game was lost, the card was not drawn, or a
gameplay decision was poor. Returning no deck change is a valid and often preferable
result.

The tool distinguishes **confirmed**, **likely**, and **possible** findings. Seeing a
legal action in the log does not by itself mean that action was strategically better.

## Privacy and persistence

- Raw `Player.log` content is parsed in memory and is never copied into review history.
- Match identifiers are converted to short SHA-256-based keys before persistence.
- Submitted deck composition is represented in history by a one-way fingerprint;
  full main-deck and sideboard lists are parsed in memory rather than persisted.
- Account identifiers, opponent names, local paths, and unrelated history are neither
  stored nor included in the Codex request.
- The Codex payload is bounded to observable decisions plus the local card definitions
  required to interpret them.
- Codex runs ephemerally with a read-only sandbox and tools, browsing, apps, computer
  control, image generation, and multi-agent features disabled.
- On macOS, history lives at
  `~/Library/Application Support/MTGA_Draft_Tool/GameReviews/history.json`.

## Limitations

- Arena logs do not expose hidden cards, private opponent decisions, player intent, or
  every replacement-effect and priority nuance. These gaps lower certainty.
- A game logged without detailed gameplay events can appear with summary or partial
  coverage but cannot receive a reliable deep review.
- Rotated or truncated Arena logs cannot be reconstructed after their events are gone.
- Unknown card identifiers reduce review quality until a matching local dataset is
  available.
- The coaching is advisory. It cannot guarantee an optimal line and never clicks or
  controls Arena.
- Deck changes can expose correlations, not prove causation. Synergy, matchup, and
  small-sample effects may still make a suggested swap wrong; certainty and evidence
  counts are displayed for that reason.

If no games appear, verify the selected `Player.log` path in Preferences and confirm
that Detailed Logs remained enabled for the entire game.
