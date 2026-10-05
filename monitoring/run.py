"""Sample, judge, and score one monitoring window.

Usage:
    uv run python -m monitoring.run --period before
    uv run python -m monitoring.run --period after
    uv run python -m monitoring.run --last-hours 24
    uv run python -m monitoring.run --period after --dry-run

A configured period is one complete run of the 50 monitoring scenarios, so
its traces are grouped by scenario ID and the period is rejected if a scenario
is missing, a scenario mixes separate sessions, or a trace used another model.
The scheduled ``--last-hours`` window groups traces by ``meta.session_id``.
Only the random sample feeds the corrected failure rate. Risk group verdicts
are written separately for inspection.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.config import REPO_ROOT
from analysis.run_judges import _clean_message, _flatten
from monitoring.chart import prevalence_chart
from monitoring.correct import corrected_mode_prevalence
from monitoring.run_judges import judge_sample, judge_test_data, load_monitoring_judge
from monitoring.sample import DEFAULT_RISK_GROUPS, select_traces
from monitoring.write_scores import build_score_records, post_scores

MONITORING = REPO_ROOT / "monitoring"
CONFIG_PATH = MONITORING / "config.json"
HISTORY_PATH = MONITORING / "history.jsonl"
CHART_PATH = MONITORING / "prevalence.svg"
OUTPUT_DIR = MONITORING / "output"
SCENARIOS_PATH = REPO_ROOT / "scenarios" / "monitoring_scenarios.jsonl"
SAMPLE_SEED = 7


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    unknown = set(config["risk_groups"]) - set(DEFAULT_RISK_GROUPS)
    if unknown:
        raise ValueError(f"unknown risk groups in config: {sorted(unknown)}")
    if not 0 < config["random_rate"] <= 1:
        raise ValueError("random_rate must be in (0, 1]")
    return config


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _bare_model(model: str) -> str:
    return model.split("/", 1)[-1]


def fetch_window(start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Fetch and normalize every Langfuse trace whose timestamp is in [start, end]."""
    from analysis.helpers import langfuse_io
    from analysis.helpers.normalization import normalize_trace

    if not langfuse_io.is_configured():
        raise langfuse_io.LangfuseNotConfigured(
            "set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and LANGFUSE_HOST"
        )
    lf = langfuse_io._client()
    summaries: list[Any] = []
    page = 1
    while True:
        batch = list(
            lf.api.trace.list(
                page=page, limit=100, from_timestamp=start, to_timestamp=end
            ).data
            or []
        )
        summaries.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return [normalize_trace(lf.api.trace.get(summary.id)) for summary in summaries]


def check_models(traces: list[dict[str, Any]], model: str) -> None:
    for trace in traces:
        observed = {_bare_model(m) for m in trace.get("models") or []}
        if observed != {_bare_model(model)}:
            raise ValueError(
                f"trace {trace['id']} used models {sorted(observed) or 'none'}, "
                f"expected only {model}"
            )


def build_conversation(key: str, traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine one conversation's ordered traces into a judge-ready record.

    The text uses the Homework 5 judge format: ordered user, assistant, tool
    call, and tool result messages, with no labels or scenario metadata.
    """
    ordered = sorted(traces, key=lambda t: str(t.get("timestamp") or ""))
    # The Homework 5 export stored tool payloads with sorted keys.
    messages = [
        json.loads(json.dumps(cleaned, sort_keys=True, default=str))
        for trace in ordered
        for message in trace.get("trace") or []
        if (cleaned := _clean_message(message))
    ]
    return {
        "id": ordered[-1]["id"],
        "timestamp": ordered[-1]["timestamp"],
        "conversation": key,
        "trace_ids": [t["id"] for t in ordered],
        "text": _flatten(messages),
        "tools": sorted(
            {str(m["name"]) for m in messages if m["role"] == "tool_call" and m.get("name")}
        ),
        "turn_count": sum(m["role"] == "user" for m in messages),
    }


def period_conversations(
    traces: list[dict[str, Any]], model: str, scenario_ids: list[str]
) -> tuple[list[dict[str, Any]], int]:
    """Validate one complete scenario run and return its 50 conversations."""
    wanted = set(scenario_ids)
    eligible = [t for t in traces if t["meta"].get("scenario_id") in wanted]
    by_scenario: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in eligible:
        by_scenario[trace["meta"]["scenario_id"]].append(trace)
    missing = sorted(wanted - set(by_scenario))
    if missing:
        raise ValueError(f"period is missing {len(missing)} scenario IDs, e.g. {missing[:5]}")
    for scenario_id, group in by_scenario.items():
        sessions = {t["meta"].get("session_id") for t in group} - {None}
        if len(sessions) > 1:
            raise ValueError(
                f"scenario {scenario_id} has traces from {len(sessions)} sessions; "
                "separate retries cannot be combined into one period"
            )
    check_models(eligible, model)
    conversations = [build_conversation(sid, by_scenario[sid]) for sid in sorted(by_scenario)]
    return conversations, len(eligible)


def session_conversations(
    traces: list[dict[str, Any]], model: str
) -> tuple[list[dict[str, Any]], int]:
    """Group a rolling window's traces into conversations by session ID."""
    eligible = [t for t in traces if t["meta"].get("session_id")]
    check_models(eligible, model)
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in eligible:
        by_session[trace["meta"]["session_id"]].append(trace)
    conversations = [build_conversation(sid, by_session[sid]) for sid in sorted(by_session)]
    return conversations, len(eligible)


def place_scores(
    records: list[dict[str, Any]],
    conversations: dict[str, dict[str, Any]],
    label: str,
    end: datetime,
) -> list[dict[str, Any]]:
    """Date each score at its conversation and attach the period score to a session.

    Dating verdicts by conversation keeps the two periods apart on a Langfuse
    time series. The period's prevalence score has no trace, so it is attached
    to the session ``monitoring-<label>`` and dated at the end of the window.
    """
    placed = []
    for record in records:
        record = dict(record)
        if record["trace_id"] is None:
            record["session_id"] = f"monitoring-{label}"
            record["timestamp"] = end
        else:
            record["timestamp"] = _parse_utc(conversations[record["trace_id"]]["timestamp"])
        placed.append(record)
    return placed


def upsert_history(record: dict[str, Any], path: Path = HISTORY_PATH) -> list[dict[str, Any]]:
    """Keep one history line per label, in first-seen order."""
    rows = []
    if path.exists():
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for index, row in enumerate(rows):
        if row["label"] == record["label"]:
            rows[index] = record
            break
    else:
        rows.append(record)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return rows


def write_output(label: str, payload: dict[str, Any]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{label}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def run(
    label: str,
    start: datetime,
    end: datetime,
    config: dict[str, Any],
    *,
    by_scenario: bool,
    dry_run: bool = False,
) -> int:
    judge_id, mode, model = config["judge_id"], config["judge_mode"], config["model"]
    judge = load_monitoring_judge(judge_id)
    if judge.get("mode") != mode:
        raise ValueError(f"judge {judge_id} measures {judge.get('mode')}, not {mode}")

    traces = fetch_window(start, end)
    if by_scenario:
        scenario_ids = [
            json.loads(line)["id"]
            for line in SCENARIOS_PATH.read_text().splitlines()
            if line.strip()
        ]
        conversations, trace_count = period_conversations(traces, model, scenario_ids)
        if len(conversations) != len(scenario_ids):
            raise ValueError(f"expected {len(scenario_ids)} conversations, got {len(conversations)}")
    else:
        conversations, trace_count = session_conversations(traces, model)

    window = {"from": start.isoformat(), "to": end.isoformat()}
    base = {
        "label": label,
        "judge_id": judge_id,
        "judge_mode": mode,
        "model": model,
        "window": window,
        "langfuse_traces": trace_count,
        "conversations": len(conversations),
    }
    print(f"[{label}] window {window['from']} -> {window['to']}")
    print(f"[{label}] {trace_count} eligible traces, {len(conversations)} conversations")

    if not conversations:
        write_output(label, {**base, "random_sample": 0, "risk_sample": 0, "judge_calls": 0})
        print(f"[{label}] no eligible conversations; the judge was not called")
        return 0

    groups = {name: DEFAULT_RISK_GROUPS[name] for name in config["risk_groups"]}
    plan = select_traces(conversations, config["random_rate"], groups, seed=SAMPLE_SEED)
    risk_members = list(
        dict.fromkeys(t["id"] for members in plan["risk_groups"].values() for t in members)
    )
    print(
        f"[{label}] random sample {len(plan['random'])}, "
        + ", ".join(f"{name} {len(m)}" for name, m in plan["risk_groups"].items())
        + f"; judge calls {len(plan['to_judge'])} on {judge['model']} ({judge_id})"
    )
    if dry_run:
        print(f"[{label}] dry run: stopping before the judge")
        return 0

    verdicts = judge_sample(judge_id, plan["to_judge"])
    random_verdicts = {t["id"]: verdicts[t["id"]] for t in plan["random"]}
    risk_verdicts = {trace_id: verdicts[trace_id] for trace_id in risk_members}
    test_labels, test_preds = judge_test_data(judge_id)
    estimate = corrected_mode_prevalence(list(random_verdicts.values()), test_labels, test_preds)
    by_id = {c["id"]: c for c in conversations}
    written = post_scores(
        place_scores(
            build_score_records(mode, random_verdicts, risk_verdicts, estimate, label),
            by_id,
            label,
            end,
        )
    )

    write_output(
        label,
        {
            **base,
            "estimate": estimate,
            "random": [
                {"trace_id": tid, "conversation": by_id[tid]["conversation"], "verdict": v}
                for tid, v in random_verdicts.items()
            ],
            "risk_groups": {
                name: [
                    {"trace_id": t["id"], "conversation": t["conversation"], "verdict": verdicts[t["id"]]}
                    for t in members
                ]
                for name, members in plan["risk_groups"].items()
            },
            "scores_written": written,
        },
    )
    history_path, chart_path = (
        (HISTORY_PATH, CHART_PATH)
        if by_scenario
        else (OUTPUT_DIR / "history.jsonl", OUTPUT_DIR / "prevalence.svg")
    )
    history = upsert_history(
        {
            **base,
            "random_sample": len(random_verdicts),
            "risk_sample": len(risk_verdicts),
            "raw": estimate["raw"],
            "corrected": estimate["corrected"],
            "ci_low": estimate["ci_low"],
            "ci_high": estimate["ci_high"],
            "failure_sensitivity": estimate["failure_sensitivity"],
            "pass_specificity": estimate["pass_specificity"],
            "random_flagged": sum(random_verdicts.values()),
            "risk_flagged": sum(risk_verdicts.values()),
        },
        history_path,
    )
    chart_path.write_text(
        prevalence_chart(history, threshold=config["threshold"], mode=mode), encoding="utf-8"
    )
    print(
        f"[{label}] raw {estimate['raw']}, corrected {estimate['corrected']} "
        f"(95% CI {estimate['ci_low']}-{estimate['ci_high']}); "
        f"risk flagged {sum(risk_verdicts.values())}/{len(risk_verdicts)}; "
        f"{written} scores written"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    window = parser.add_mutually_exclusive_group(required=True)
    window.add_argument("--period", help="a period label from monitoring/config.json")
    window.add_argument("--last-hours", type=float, help="monitor the trailing N hours")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--dry-run", action="store_true", help="stop before calling the judge")
    args = parser.parse_args(argv)

    from observability.instrument import load_env

    load_env()
    config = load_config(args.config)
    if args.period:
        matches = [p for p in config["periods"] if p["label"] == args.period]
        if not matches:
            parser.error(f"no period {args.period!r} in {args.config}")
        period = matches[0]
        return run(
            period["label"],
            _parse_utc(period["from"]),
            _parse_utc(period["to"]),
            config,
            by_scenario=True,
            dry_run=args.dry_run,
        )
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=args.last_hours)
    label = f"last{args.last_hours:g}h-{end:%Y-%m-%dT%H%MZ}"
    return run(label, start, end, config, by_scenario=False, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
