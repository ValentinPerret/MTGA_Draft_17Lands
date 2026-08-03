"""Repeatable local benchmark for the contextual draft hot path.

This intentionally excludes optional Codex latency because that work is asynchronous
and cannot delay the deterministic recommendation.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.advisor_v2.codex_reviewer import build_review_request
from src.advisor_v2.service import ContextualDraftAdvisor
from src.limited_sets import SetDictionary, SetInfo
from src.log_scanner import ArenaScanner

REPLAY = ROOT / "tests/data/innistrad_planar_cube_disabled_detailed_logs.log"


def card(name, win_rate, colors, cmc=3):
    stats = {
        "gihwr": win_rate,
        "ohwr": win_rate - 1,
        "gpwr": win_rate - 2,
        "iwd": win_rate - 54,
        "alsa": 4.0,
        "samples": 12_000,
    }
    return {
        "name": name,
        "arena_ids": [abs(hash(name)) % 100_000 + 1],
        "colors": colors,
        "types": ["Creature"],
        "cmc": cmc,
        "mana_cost": "" if not colors else f"{{{max(0, cmc - 1)}}}{{{colors[0]}}}",
        "tags": [],
        "oracle_text": "",
        "deck_colors": {"All Decks": stats, "WU": stats, "UB": stats},
    }


def metrics():
    result = MagicMock()
    result.get_metrics.side_effect = (
        lambda _color, field: (54.0, 4.0) if field == "gihwr" else (0.0, 0.0)
    )
    return result


def percentile(values, fraction):
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


def measure(function, iterations):
    function()  # Warm caches once.
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        function()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples), percentile(samples, 0.95)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args()
    iterations = max(3, args.iterations)

    draft_pool = [
        card(
            f"Pool {index}",
            53.0 + (index % 8),
            ["W"] if index % 2 else ["U"],
            (index % 6) + 1,
        )
        for index in range(30)
    ]
    pack = [
        card(
            f"Candidate {index}",
            52.0 + (index % 10),
            ["W"] if index % 3 else ["U"],
            (index % 6) + 1,
        )
        for index in range(15)
    ]
    advisor = ContextualDraftAdvisor(metrics(), draft_pool)

    def evaluate_pack():
        return advisor.evaluate_pack(pack, 5, 2)

    recommendations = evaluate_pack()

    def build_state():
        return build_review_request(
            event_name="CubeDraft_Planar_Innistrad",
            pack_number=2,
            pick_number=5,
            pack_cards=pack,
            pool_cards=draft_pool,
            recommendations=recommendations,
        )

    sets = SetDictionary(
        data={
            "Cube Planar Innistrad": SetInfo(set_code="CUBEPLANARINNISTRAD")
        }
    )
    temporary = tempfile.TemporaryDirectory(prefix="mtga-benchmark-")

    def parse_replay():
        scanner = ArenaScanner(str(REPLAY), sets, retrieve_unknown=False)
        scanner.state_file = str(Path(temporary.name) / "state.json")
        scanner.clear_draft(True)
        scanner.draft_start_search()
        scanner.draft_data_search()

    benchmarks = {
        "parse sanitized replay": measure(parse_replay, iterations),
        "evaluate 15-card pack": measure(evaluate_pack, iterations),
        "build minimized review state": measure(build_state, iterations),
    }
    temporary.cleanup()

    print(f"Contextual copilot benchmark ({iterations} iterations)")
    for label, (median_ms, p95_ms) in benchmarks.items():
        print(f"{label:31} median={median_ms:8.2f} ms  p95={p95_ms:8.2f} ms")


if __name__ == "__main__":
    main()
