"""Build student review-app state from Homework 3 traces and HW4 notes.

Writes student_samples.json, student_graph.json, and reshapes
student_suggestions.json so analysis/server.py --student can serve them
without touching the demo fixtures the tests copy.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from analysis.helpers.normalization import normalize_traces

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(__file__).resolve().parent / "state"
EXPORT = ROOT / "traces" / "support_traces.json"
SCENARIOS = ROOT / "scenarios" / "support_scenarios.jsonl"


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def main() -> None:
    raw = json.loads(EXPORT.read_text())
    records = raw["traces"] if isinstance(raw, dict) else raw
    seen: set[str] = set()
    unique_records = []
    for rec in records:
        tid = str(rec.get("id") or rec.get("trace_id") or "")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        unique_records.append(rec)
    traces = normalize_traces(unique_records)
    raw_by_id = {str(rec.get("id") or rec.get("trace_id") or ""): rec for rec in unique_records}

    scens = {}
    for line in SCENARIOS.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            scens[rec["id"]] = rec

    anns = _load_json(STATE / "student_annotations.json", {}).get("annotations", [])
    sugg_doc = _load_json(STATE / "student_suggestions.json", {"suggestions": []})
    raw_sugg = sugg_doc.get("suggestions", []) if isinstance(sugg_doc, dict) else sugg_doc

    annotated_ids = {a.get("trace_id") for a in anns if a.get("trace_id")}
    sugg_ids = {s.get("trace_id") for s in raw_sugg if s.get("trace_id")}
    priority = [t for t in traces if t["trace_id"] in annotated_ids | sugg_ids]
    rest = [t for t in traces if t["trace_id"] not in annotated_ids | sugg_ids]
    ordered = priority + rest

    samples = []
    for trace in ordered:
        meta = dict(trace.get("meta") or {})
        sid = meta.get("scenario_id")
        scene = scens.get(sid) or {}
        tup = scene.get("tuple") or {}
        if tup.get("role") and not meta.get("role"):
            meta["role"] = tup["role"]
        meta["intent"] = tup.get("intent")
        meta["scenario_id"] = sid
        raw = raw_by_id.get(trace["trace_id"]) or {}
        langfuse_session = raw.get("sessionId") or raw.get("session_id")
        attrs = (raw.get("metadata") or {}).get("attributes") or {}
        if isinstance(attrs, dict):
            langfuse_session = langfuse_session or attrs.get("cartwheel.session_id")
        # HW4: group turns by session_id. Export has sessionId=null, so
        # scenario_id is the conversation key for the HW3 runner.
        meta["session_id"] = langfuse_session or None
        meta["session_key"] = langfuse_session or sid or trace["trace_id"]
        meta["created_at"] = raw.get("createdAt") or raw.get("timestamp") or trace.get("timestamp")
        flags = []
        if trace["trace_id"] in annotated_ids:
            flags.append("open-coded")
        if trace["trace_id"] in sugg_ids:
            flags.append("has suggestion")
        samples.append(
            {
                "trace_id": trace["trace_id"],
                "reason": (
                    "open-coded first-failure note"
                    if trace["trace_id"] in annotated_ids
                    else "depth-scan suggestion"
                    if trace["trace_id"] in sugg_ids
                    else "remainder of the 250-trace store"
                ),
                "trace": trace["trace"],
                "text": trace.get("text"),
                "features": trace.get("features") or {},
                "meta": {k: v for k, v in meta.items() if v is not None},
                "permalink": trace.get("permalink"),
                "flags": flags,
            }
        )

    ui_sugg = []
    for i, s in enumerate(raw_sugg):
        quote = s.get("quote") or s.get("text") or ""
        ui_sugg.append(
            {
                "id": s.get("id") or f"sugg-{i}-{s.get('scenario_id') or i}",
                "trace_id": s.get("trace_id"),
                "scenario_id": s.get("scenario_id"),
                "mode": s.get("mode"),
                "quote": quote,
                "text": quote,
                "start": s.get("start"),
                "end": s.get("end"),
                "note": s.get("note"),
                "status": s.get("status") or "pending",
            }
        )

    # 2D map: intent hashed to cluster, features as axes.
    intents = sorted({(s.get("meta") or {}).get("intent") or "other" for s in samples})
    intent_ix = {name: i for i, name in enumerate(intents)}
    nodes = []
    for s in samples:
        feat = s.get("features") or {}
        intent = (s.get("meta") or {}).get("intent") or "other"
        cluster = intent_ix.get(intent, 0)
        nodes.append(
            {
                "trace_id": s["trace_id"],
                "x": float(feat.get("tool_call_count") or 0) + cluster * 0.15,
                "y": float(feat.get("turn_count") or 1) + (hash(s["trace_id"]) % 7) * 0.08,
                "cluster": cluster,
                "meta": s.get("meta") or {},
            }
        )
    clusters = [{"id": i, "label": name} for name, i in intent_ix.items()]

    (STATE / "student_samples.json").write_text(json.dumps(samples, indent=2) + "\n")
    (STATE / "student_graph.json").write_text(
        json.dumps({"nodes": nodes, "clusters": clusters}, indent=2) + "\n"
    )
    sugg_doc["suggestions"] = ui_sugg
    (STATE / "student_suggestions.json").write_text(json.dumps(sugg_doc, indent=2) + "\n")
    print(f"samples {len(samples)} (priority {len(priority)})")
    print(f"suggestions {len(ui_sugg)}")
    print(f"graph nodes {len(nodes)}")


if __name__ == "__main__":
    main()
