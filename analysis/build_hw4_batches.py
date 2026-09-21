"""Build the Homework 4 Part B four-batch review set.

Does not call select_traces(), which would overwrite the demo samples.json.
Uses analysis.helpers.selection.select for uniform and cluster picks.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from analysis.helpers import selection

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(__file__).resolve().parent / "state"
EXPORT = ROOT / "traces" / "support_traces.json"


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _role(trace: dict) -> str:
    meta = trace.get("meta") or {}
    if meta.get("role"):
        return str(meta["role"])
    md = trace.get("metadata") or {}
    attrs = md.get("attributes") if isinstance(md.get("attributes"), dict) else md
    return str(attrs.get("cartwheel.user_role") or attrs.get("role") or "unknown")


def _is_pass(note: str) -> bool:
    n = (note or "").strip().lower()
    return n.startswith("no miss") or n.startswith("re-review: no miss") or "no failure" in n[:40]


def main() -> None:
    traces = selection.load_traces(EXPORT)
    by_id = {t["id"]: t for t in traces}
    anns = _load_json(STATE / "student_annotations.json", {}).get("annotations", [])
    coded = {a["trace_id"] for a in anns if a.get("trace_id")}
    pass_ids = {a["trace_id"] for a in anns if a.get("trace_id") and _is_pass(a.get("note") or "")}
    sugg = _load_json(STATE / "student_suggestions.json", {}).get("suggestions", [])
    sugg_ids = []
    seen_sugg: set[str] = set()
    for s in sugg:
        tid = s.get("trace_id")
        if tid and tid not in seen_sugg:
            seen_sugg.add(tid)
            sugg_ids.append(tid)

    used: set[str] = set()
    batches: list[dict] = []

    def attach(picks: list[dict], extra: dict | None = None) -> list[dict]:
        out = []
        for p in picks:
            tid = p["trace_id"]
            meta = (by_id.get(tid) or {}).get("meta") or {}
            row = {
                **p,
                "scenario_id": meta.get("scenario_id"),
                "role": _role(by_id.get(tid) or {}),
                "already_open_coded": tid in coded,
            }
            if extra:
                row.update(extra)
            out.append(row)
            used.add(tid)
        return out

    # Batch 1a: 15 cluster representatives. diversity k=23 yields n_rep=15.
    div = selection.select(traces, k=23, strategy="diversity", exclude_ids=set(), seed=7)
    cluster = [p for p in div if str(p.get("reason", "")).startswith("cluster")][:15]
    batches.append(
        {
            "name": "batch1_cluster",
            "k": 15,
            "method": "kmeans cluster representatives on turn/tool/retrieval/token features",
            "picks": attach(cluster),
        }
    )

    # Batch 1b: 15 uniform, excluding cluster picks.
    uniform = selection.select(traces, k=15, strategy="random", exclude_ids=set(used), seed=7)
    batches.append(
        {
            "name": "batch1_uniform",
            "k": 15,
            "method": "uniform random (selection.select random, seed=7)",
            "picks": attach(uniform),
        }
    )

    # Batch 2: role chosen before outcomes. 10 shopper / 10 merchant / 10 support.
    by_role: dict[str, list[str]] = defaultdict(list)
    for t in traces:
        if t["id"] in used:
            continue
        by_role[_role(t)].append(t["id"])
    rng = random.Random(11)
    role_picks: list[dict] = []
    for role, n in (("shopper", 10), ("merchant", 10), ("support", 10)):
        pool = list(by_role.get(role) or [])
        rng.shuffle(pool)
        for tid in pool[:n]:
            role_picks.append(
                {
                    "trace_id": tid,
                    "reason": f"stratified on user role={role}",
                }
            )
    batches.append(
        {
            "name": "batch2_role",
            "k": 30,
            "method": "product dimension cartwheel.user_role, 10 per role, seed=11",
            "picks": attach(role_picks),
        }
    )

    # Batch 3: depth-search positives + close negatives. Not failure-only.
    depth: list[dict] = []
    for tid in sugg_ids:
        if tid in used or tid not in by_id:
            continue
        depth.append(
            {
                "trace_id": tid,
                "reason": "depth-scan candidate for a named mode",
                "kind": "depth_positive",
            }
        )
        if len([d for d in depth if d["kind"] == "depth_positive"]) >= 15:
            break
    for a in anns:
        tid = a.get("trace_id")
        if not tid or tid in used or tid in {d["trace_id"] for d in depth}:
            continue
        if tid not in pass_ids:
            continue
        depth.append(
            {
                "trace_id": tid,
                "reason": "close negative: reviewed no-miss on a similar request",
                "kind": "close_negative",
            }
        )
        if len(depth) >= 25:
            break
    if len(depth) < 25:
        for t in traces:
            if t["id"] in used or t["id"] in {d["trace_id"] for d in depth}:
                continue
            depth.append(
                {
                    "trace_id": t["id"],
                    "reason": "depth-batch fill: remaining store after exclusions",
                    "kind": "fill",
                }
            )
            if len(depth) >= 25:
                break
    depth = depth[:25]
    batches.append(
        {
            "name": "batch3_depth",
            "k": 25,
            "method": "depth search for named modes plus close negatives from no-miss notes",
            "picks": attach(depth),
        }
    )

    # Batch 4: 15 more uniform after the taxonomy, to check new modes.
    holdout = selection.select(traces, k=15, strategy="random", exclude_ids=set(used), seed=13)
    batches.append(
        {
            "name": "batch4_holdout_uniform",
            "k": 15,
            "method": "uniform random after taxonomy freeze (seed=13)",
            "picks": attach(holdout),
        }
    )

    review_ids = [p["trace_id"] for b in batches for p in b["picks"]]
    assert len(review_ids) == len(set(review_ids)) == 100, (
        len(review_ids),
        len(set(review_ids)),
    )
    still = [p for b in batches for p in b["picks"] if not p["already_open_coded"]]
    coded_n = 100 - len(still)

    manifest = {
        "source": str(EXPORT.relative_to(ROOT)),
        "selected_at": datetime.now(timezone.utc).isoformat(),
        "handout": "Part B four batches, 100 distinct traces, no double count",
        "dimension": "cartwheel.user_role",
        "already_open_coded": coded_n,
        "still_to_open_code": len(still),
        "batches": batches,
        "still_to_open_code_ids": [
            {
                "trace_id": p["trace_id"],
                "scenario_id": p.get("scenario_id"),
                "batch": b["name"],
                "reason": p.get("reason"),
            }
            for b in batches
            for p in b["picks"]
            if not p["already_open_coded"]
        ],
    }
    path = STATE / "sample_manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {path}")
    print(f"review set 100 · already coded {coded_n} · still to open-code {len(still)}")
    for b in batches:
        n = len(b["picks"])
        c = sum(1 for p in b["picks"] if p["already_open_coded"])
        print(f"  {b['name']}: {n} ({c} coded, {n - c} new)")


if __name__ == "__main__":
    main()
