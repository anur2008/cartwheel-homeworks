"""Homework 5 judge pipeline: inputs, split, development, and later test.

Part B writes judge-safe conversation records and a 20/40/40 split for
``refund_ineligible_without_why``. Human labels stay out of the judge input.
Part C registers a prompt, scores the development split, and saves metrics.
Paid DocETL batches require ``confirm=True`` after the model and trace count
are shown. Part D freezes the chosen development version and scores test once.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from datetime import datetime, timezone

from analysis.helpers import _state, guards
from analysis.helpers.normalization import normalize_traces
from analysis.helpers.tools import (
    _load_labels,
    freeze_judge,
    judge_alignment,
    register_judge,
    run_judge,
)

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "analysis" / "state"
EXPORT = ROOT / "traces" / "support_traces.json"
MODE = "refund_ineligible_without_why"
INPUTS_PATH = STATE / "hw5_trace_inputs.json"
HW5_LABELS = STATE / "hw5_labels" / f"{MODE}.jsonl"
KEEP_ROLES = {"user", "assistant", "tool_call", "tool_result"}
JUDGE_MODEL = "gpt-4o-mini"
PROMPT_V0 = ROOT / "analysis" / "prompts" / f"{MODE}-v0.txt"
REPORT_DIR = ROOT / "analysis" / "report"


def _plain_text(value: Any) -> str:
    """Unwrap Langfuse part-lists into the visible user or assistant string."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return value
    chunks: list[str] = []

    def take(node: Any) -> None:
        if isinstance(node, str):
            chunks.append(node)
            return
        if isinstance(node, dict):
            if isinstance(node.get("content"), str) and node.get("type") in (
                None,
                "text",
            ):
                chunks.append(node["content"])
                return
            for key in ("parts", "content", "text"):
                if key in node:
                    take(node[key])
                    return
            return
        if isinstance(node, list):
            for item in node:
                take(item)

    take(parsed)
    joined = "\n".join(part for part in chunks if part).strip()
    return joined or value


def _clean_message(message: dict[str, Any]) -> dict[str, Any] | None:
    role = str(message.get("role") or "")
    if role not in KEEP_ROLES:
        return None
    if role == "user":
        return {"role": "user", "text": _plain_text(message.get("text"))}
    if role == "assistant":
        return {"role": "assistant", "text": _plain_text(message.get("text"))}
    if role == "tool_call":
        return {
            "role": "tool_call",
            "name": message.get("name"),
            "arguments": message.get("arguments"),
        }
    return {
        "role": "tool_result",
        "name": message.get("name"),
        "content": message.get("content"),
    }


def _flatten(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role") or "step")
        if role == "tool_call":
            content = json.dumps(message.get("arguments"), ensure_ascii=False, default=str)
        elif role == "tool_result":
            content = json.dumps(message.get("content"), ensure_ascii=False, default=str)
        else:
            content = str(message.get("text") or "")
        if content:
            parts.append(f"{role}: {content}")
    return "\n".join(parts)


def _load_export() -> list[dict[str, Any]]:
    raw = json.loads(EXPORT.read_text())
    records = raw["traces"] if isinstance(raw, dict) else raw
    return normalize_traces(records)


def _primary_trace_ids() -> list[str]:
    """One labeled trace per conversation (later turn when a scenario has two)."""
    rows = [
        json.loads(line)
        for line in HW5_LABELS.read_text().splitlines()
        if line.strip()
    ]
    traces = {t["trace_id"]: t for t in _load_export()}
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("scenario_id") or row["trace_id"])].append(row["trace_id"])
    chosen: list[str] = []
    for tids in grouped.values():
        tids_sorted = sorted(
            tids,
            key=lambda tid: str((traces.get(tid) or {}).get("timestamp") or ""),
        )
        chosen.append(tids_sorted[-1])
    if len(chosen) != len(set(chosen)):
        raise ValueError("duplicate primary trace ids")
    return chosen


def prepare_inputs(mode: str = MODE) -> list[dict[str, Any]]:
    """Write one judge-safe conversation record per eligible HW5 label."""
    if mode != MODE:
        raise ValueError(f"Part B is wired for {MODE}, not {mode}")
    export = _load_export()
    by_id = {t["trace_id"]: t for t in export}
    by_sid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in export:
        sid = (trace.get("meta") or {}).get("scenario_id") or trace["trace_id"]
        by_sid[str(sid)].append(trace)
    for sid in by_sid:
        by_sid[sid].sort(key=lambda t: str(t.get("timestamp") or ""))

    records: list[dict[str, Any]] = []
    for tid in _primary_trace_ids():
        primary = by_id[tid]
        sid = (primary.get("meta") or {}).get("scenario_id") or tid
        messages: list[dict[str, Any]] = []
        for turn in by_sid[str(sid)]:
            for message in turn.get("trace") or []:
                cleaned = _clean_message(message)
                if cleaned:
                    messages.append(cleaned)
        records.append(
            {
                "trace_id": tid,
                "trace": messages,
                "text": _flatten(messages),
            }
        )

    leaked = ["label", "note", "annotation", "scenario_id", "class", "source"]
    for record in records:
        if any(key in record for key in leaked):
            raise ValueError(f"judge input leaked metadata: {record['trace_id']}")
    INPUTS_PATH.write_text(json.dumps(records, indent=2) + "\n")
    return records


def split_data(mode: str = MODE, *, overwrite: bool = False) -> dict[str, list[str]]:
    """Stratified 20/40/40 split on independent conversations.

    The helper ``split_labels`` has no ``eligible_trace_ids`` argument and
    would also split both turns of ``support-0113`` / ``support-0127``. This
    function uses the same algorithm on the Part B conversation ids, and
    writes only ``splits.json[mode]`` so the demo
    ``unsupported_policy_claim`` split stays in place.
    """
    if mode != MODE:
        raise ValueError(f"Part B is wired for {MODE}, not {mode}")
    splits_file = _state.read_json(_state.state_path("splits.json"), default={})
    existing = splits_file.get(mode)
    if existing and not overwrite:
        return {
            "train": list(existing["train"]),
            "dev": list(existing["dev"]),
            "test": list(existing["test"]),
        }
    records = json.loads(INPUTS_PATH.read_text())
    eligible = [record["trace_id"] for record in records]
    stored = {row["trace_id"]: int(row["label"]) for row in _load_labels(mode)}
    fails = sorted(tid for tid in eligible if stored.get(tid) == 1)
    passes = sorted(tid for tid in eligible if stored.get(tid) == 0)
    missing = [tid for tid in eligible if tid not in stored]
    if missing:
        raise ValueError(f"no HW4 label for {missing[:5]}")

    fractions = (0.20, 0.40, 0.40)
    guards.check_split_class_counts(len(fails), len(passes), 10, fractions)
    rng = random.Random(7)

    def partition(items: list[str]) -> tuple[list[str], list[str], list[str]]:
        ordered = list(items)
        rng.shuffle(ordered)
        n_train = round(len(ordered) * fractions[0])
        n_dev = round(len(ordered) * fractions[1])
        train = ordered[:n_train]
        dev = ordered[n_train : n_train + n_dev]
        test = ordered[n_train + n_dev :]
        return train, dev, test

    f_train, f_dev, f_test = partition(fails)
    p_train, p_dev, p_test = partition(passes)
    assignment = {
        "train": sorted(f_train + p_train),
        "dev": sorted(f_dev + p_dev),
        "test": sorted(f_test + p_test),
    }
    combined = assignment["train"] + assignment["dev"] + assignment["test"]
    if len(combined) != len(set(combined)):
        raise ValueError("splits overlap")
    if set(combined) != set(eligible):
        raise ValueError("splits do not cover every eligible conversation")

    splits_file[mode] = {
        **assignment,
        "seed": 7,
        "fractions": list(fractions),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "eligible_trace_ids": eligible,
        "note": "one conversation per scenario; HW4 stored 1=failure present",
    }
    _state.write_json(_state.state_path("splits.json"), splits_file)
    return assignment


def _class_counts(ids: list[str], stored: dict[str, int]) -> tuple[int, int]:
    fail = sum(1 for tid in ids if stored[tid] == 1)
    pas = sum(1 for tid in ids if stored[tid] == 0)
    return pas, fail


def _split_ids(mode: str, split: str) -> list[str]:
    splits = json.loads((STATE / "splits.json").read_text())
    ids = splits.get(mode, {}).get(split)
    if not ids:
        raise ValueError(f"no '{split}' split for mode '{mode}'")
    return list(ids)


def development_batch_plan(mode: str = MODE, prompt_path: str | Path = PROMPT_V0) -> dict[str, Any]:
    """Show model and development trace count before a paid DocETL batch."""
    prompt_path = Path(prompt_path)
    ids = _split_ids(mode, "dev")
    stored = {row["trace_id"]: int(row["label"]) for row in _load_labels(mode)}
    pas, fail = _class_counts(ids, stored)
    return {
        "mode": mode,
        "prompt_path": str(prompt_path),
        "model": JUDGE_MODEL,
        "split": "dev",
        "n_traces": len(ids),
        "n_pass": pas,
        "n_fail": fail,
        "trace_source_env": "CARTWHEEL_JUDGE_TRACE_SOURCE",
        "note": "Paid DocETL batch. Call run_development(..., confirm=True) after approval.",
    }


def _require_live_backend() -> None:
    import os

    from observability.instrument import load_env

    load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set in the environment or .env")
    source = os.environ.get("CARTWHEEL_JUDGE_TRACE_SOURCE")
    expected = str(INPUTS_PATH)
    if source != expected:
        raise RuntimeError(
            "Set CARTWHEEL_JUDGE_TRACE_SOURCE to the frozen Part B inputs "
            f"before DocETL: export CARTWHEEL_JUDGE_TRACE_SOURCE={expected!r}"
        )


def test_batch_plan(judge_id: str) -> dict[str, Any]:
    """Show model and test trace count before the held-out DocETL batch."""
    ids = _split_ids(MODE, "test")
    stored = {row["trace_id"]: int(row["label"]) for row in _load_labels(MODE)}
    pas, fail = _class_counts(ids, stored)
    return {
        "judge_id": judge_id,
        "model": JUDGE_MODEL,
        "split": "test",
        "n_traces": len(ids),
        "n_pass": pas,
        "n_fail": fail,
        "note": "Paid DocETL batch after freeze. Call run_test(..., confirm=True).",
    }


def run_test(judge_id: str, *, confirm: bool = False) -> dict[str, Any]:
    """Freeze ``judge_id``, score the test split once, save metrics."""
    plan = test_batch_plan(judge_id)
    if not confirm:
        raise RuntimeError(
            "Refusing paid test batch until confirm=True. "
            f"model={plan['model']} traces={plan['n_traces']} split=test "
            f"judge_id={judge_id}"
        )
    _require_live_backend()
    freeze_judge(judge_id)
    run_judge(judge_id, split="test")
    metrics = judge_alignment(judge_id, split="test")
    report = {
        **metrics,
        "model": JUDGE_MODEL,
        "n_pass": plan["n_pass"],
        "n_fail": plan["n_fail"],
        "frozen": True,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / f"test-{judge_id}.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    report["report_path"] = str(out_path)
    return report


def run_development(
    mode: str,
    prompt_path: str | Path,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    """Register a prompt version, score the development split, save metrics.

    Does not inspect or score the test split. Requires ``confirm=True`` so a
    paid batch does not start until the model and trace count are approved.
    """
    if mode != MODE:
        raise ValueError(f"Part C is wired for {MODE}, not {mode}")
    plan = development_batch_plan(mode, prompt_path)
    if not confirm:
        raise RuntimeError(
            "Refusing paid batch until confirm=True. "
            f"model={plan['model']} traces={plan['n_traces']} split=dev "
            f"prompt={plan['prompt_path']}"
        )
    _require_live_backend()
    prompt_text = Path(prompt_path).read_text()
    registered = register_judge(mode, prompt_text, JUDGE_MODEL)
    judge_id = registered["judge_id"]
    run_judge(judge_id, split="dev")
    metrics = judge_alignment(judge_id, split="dev")
    report = {
        **registered,
        **metrics,
        "prompt_path": str(prompt_path),
        "model": JUDGE_MODEL,
        "n_pass": plan["n_pass"],
        "n_fail": plan["n_fail"],
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / f"dev-{judge_id}.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    report["report_path"] = str(out_path)
    return report


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Homework 5 judge pipeline")
    parser.add_argument(
        "step",
        nargs="?",
        default="plan",
        choices=("prepare", "split", "plan", "develop", "test"),
    )
    parser.add_argument("--prompt", default=str(PROMPT_V0))
    parser.add_argument(
        "--judge-id",
        default=f"{MODE}-v1",
        help="Judge to freeze and test (Part D default: v1)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required for develop/test: start the paid gpt-4o-mini DocETL batch",
    )
    args = parser.parse_args()
    if args.step == "prepare":
        records = prepare_inputs()
        print("inputs", len(records), "->", INPUTS_PATH)
        return
    if args.step == "split":
        assignment = split_data()
        stored = {row["trace_id"]: int(row["label"]) for row in _load_labels(MODE)}
        print("set\tpass\tfail\tn")
        for name in ("train", "dev", "test"):
            pas, fail = _class_counts(assignment[name], stored)
            print(f"{name}\t{pas}\t{fail}\t{len(assignment[name])}")
        return
    if args.step == "plan":
        print(json.dumps(development_batch_plan(MODE, args.prompt), indent=2))
        return
    if args.step == "develop":
        report = run_development(MODE, args.prompt, confirm=args.confirm)
        print(json.dumps(report, indent=2))
        return
    report = run_test(args.judge_id, confirm=args.confirm)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
