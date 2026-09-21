"""Fill Homework 4 Part D taxonomy fields and Part E 100×8 labels.

Does not overwrite demo patterns.json / annotations.json.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STATE = Path(__file__).resolve().parent / "state"
REPORT = Path(__file__).resolve().parent / "report"
TS = datetime.now(timezone.utc).isoformat()

MODES = [
    "refund_ineligible_without_why",
    "reask_refund_reasons",
    "disputes_with_refund",
    "omit_product_title",
    "escalate_offered_not_done",
    "unnecessary_escalate_offer",
    "untitled_match_as_identity",
    "invented_claim_not_in_tool",
]

# Present (1) on the Part B 100, by scenario_id. Two 0113 turns are split below.
PRESENT = {
    "refund_ineligible_without_why": {
        "support-0234", "support-0231", "support-0250", "support-0195",
        "support-0209", "support-0237", "support-0229", "support-0223",
        "support-0090", "support-0235", "support-0192", "support-0100",
        "support-0089", "support-0213", "support-0187", "support-0189",
        "support-0194", "support-0201", "support-0204", "support-0207",
        "support-0026", "support-0191", "support-0193", "support-0196",
        "support-0197", "support-0200", "support-0202", "support-0203",
        "support-0206", "support-0208", "support-0210", "support-0211",
        "support-0212", "support-0214", "support-0215", "support-0186",
        "support-0226", "support-0079", "support-0246",
    },
    "reask_refund_reasons": {
        "support-0162", "support-0159", "support-0172", "support-0153",
        "support-0173", "support-0168", "support-0154", "support-0156",
        "support-0160", "support-0185",
    },
    "disputes_with_refund": {
        "support-0048", "support-0049",
    },
    "omit_product_title": {
        "support-0090", "support-0100", "support-0089", "support-0201",
    },
    "escalate_offered_not_done": {
        "support-0137", "support-0016", "support-0019", "support-0013",
        "support-0023",
    },
    "unnecessary_escalate_offer": {
        "support-0071", "support-0056", "support-0036", "support-0059",
    },
    "untitled_match_as_identity": {
        "support-0008", "support-0007",
    },
    "invented_claim_not_in_tool": {
        "support-0073", "support-0147", "support-0013",
    },
}

# 0113 has two traces in the 100. Only the sales-comparison turn is untitled.
UNTITLED_TRACE_IDS = {"681df836beacca84c843af83f1dc856f"}


def main() -> None:
    manifest = json.loads((STATE / "sample_manifest.json").read_text())
    patterns = json.loads((STATE / "student_patterns.json").read_text())
    sugg = json.loads((STATE / "student_suggestions.json").read_text())
    picks = [p for b in manifest["batches"] for p in b["picks"]]
    assert len(picks) == 100

    extra = {
        "refund_ineligible_without_why": {
            "close_negative_scenario_ids": [
                "support-0071", "support-0242", "support-0180",
            ],
            "neighbor_mode": "reask_refund_reasons",
            "boundary": "Present when a status/details reply reports ineligible (or omits the flag) without the window or store-override reason. Absent when the reply states the window/override, or when eligibility is Yes. Not a refund write-path reask.",
            "evaluator_type": "judge",
        },
        "reask_refund_reasons": {
            "close_negative_scenario_ids": [
                "support-0180", "support-0184", "support-0043",
            ],
            "neighbor_mode": "disputes_with_refund",
            "boundary": "Present when the user already asked to refund and often already gave a reason, but the agent still asks reason/amount, or starts that Q&A on an eligibility question. Absent when no reason was given yet (write-tool prompt requires it) or the user only asked how much they would get back. A chargeback is disputes_with_refund, not this mode.",
            "evaluator_type": "judge",
        },
        "disputes_with_refund": {
            "close_negative_scenario_ids": [
                "support-0125", "support-0044", "support-0180",
            ],
            "neighbor_mode": "reask_refund_reasons",
            "boundary": "Present when a stated card-issuer dispute/chargeback is handled as a normal auto-refund. Absent when the agent cites cw-disputes and routes to a human, or when the user asked for an ordinary refund with no dispute.",
            "evaluator_type": "judge",
        },
        "omit_product_title": {
            "close_negative_scenario_ids": [
                "support-0004", "support-0173", "support-0112",
            ],
            "neighbor_mode": "untitled_match_as_identity",
            "boundary": "Present when an order/cancel reply includes product_id, quantity, or price but no item name. Absent when the reply names the SKU, or when get_order omission was left as the tool shape (0112). Untitled catalog matches are untitled_match_as_identity, not this mode.",
            "evaluator_type": "code",
        },
        "escalate_offered_not_done": {
            "close_negative_scenario_ids": [
                "support-0054", "support-0053", "support-0065",
            ],
            "neighbor_mode": "unnecessary_escalate_offer",
            "boundary": "Present when escalation is required or the agent offers a ticket, but escalate_to_human is not called this turn. Absent when a ticket is not required (account-change refusal, shipped-cancel refusal) even if an offer appears. Opposite of unnecessary_escalate_offer, where policy already resolved the request.",
            "evaluator_type": "code",
        },
        "unnecessary_escalate_offer": {
            "close_negative_scenario_ids": [
                "support-0137", "support-0054", "support-0044",
            ],
            "neighbor_mode": "escalate_offered_not_done",
            "boundary": "Present when policy or authorization already resolved the request, but the agent still offers a human or special-circumstances exception. Absent when a human is actually required (ESC-3 chronology, chargeback, inconsistent data the agent cannot fix).",
            "evaluator_type": "judge",
        },
        "untitled_match_as_identity": {
            "close_negative_scenario_ids": [
                "support-0004", "support-0184", "support-0126",
            ],
            "neighbor_mode": "invented_claim_not_in_tool",
            "boundary": "Present when an empty-title or fuzzy tool hit is treated as the product the user named. Absent when search returns titled rows and the agent asks which (0004), or when the agent reports the title is empty without asserting identity (0006, rejected suggestion). Invented stock/payment facts are invented_claim_not_in_tool.",
            "evaluator_type": "judge",
        },
        "invented_claim_not_in_tool": {
            "close_negative_scenario_ids": [
                "support-0141", "support-0125", "support-0136",
            ],
            "neighbor_mode": "untitled_match_as_identity",
            "boundary": "Present when the reply asserts a fact no tool returned (stock, payment timing, credit amount, sign-flipped price). Absent when the agent only restates tool fields or cites a retrieved policy. Cancel-without-confirm is parked, not this mode, unless a payment reversal is invented.",
            "evaluator_type": "judge",
        },
    }

    sid_by_tid = {p["trace_id"]: p.get("scenario_id") for p in picks}
    by_sid: dict[str, list[str]] = {}
    for p in picks:
        by_sid.setdefault(p.get("scenario_id"), []).append(p["trace_id"])

    def present(mode: str, tid: str, sid: str) -> int:
        if mode == "untitled_match_as_identity":
            if tid in UNTITLED_TRACE_IDS:
                return 1
            return 1 if sid in PRESENT[mode] and tid not in {
                "d194012f863f8571f52e5b5bfd9b8843"
            } else 0
        return 1 if sid in PRESENT[mode] else 0

    rows = []
    counts = {m: 0 for m in MODES}
    for p in picks:
        tid, sid = p["trace_id"], p.get("scenario_id")
        modes = {m: present(m, tid, sid) for m in MODES}
        for m, v in modes.items():
            counts[m] += v
        rows.append({"trace_id": tid, "scenario_id": sid, "modes": modes})

    labels_doc = {"rows": rows}
    (STATE / "student_labels.json").write_text(
        json.dumps(labels_doc, indent=2) + "\n"
    )
    labels_dir = STATE / "labels"
    labels_dir.mkdir(exist_ok=True)
    by_mode: dict[str, list[dict]] = {m: [] for m in MODES}
    for row in rows:
        for mode, val in row["modes"].items():
            by_mode[mode].append({
                "trace_id": row["trace_id"],
                "scenario_id": row["scenario_id"],
                "mode": mode,
                "label": val,
                "source": "human",
                "ts": TS,
            })
    for mode, recs in by_mode.items():
        (labels_dir / f"{mode}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in recs)
        )

    # Reject untitled_match on 0006: discloses empty title, does not assert identity.
    rejected = None
    for s in sugg.get("suggestions", []):
        if s.get("id") == "sugg-66-support-0006":
            s["status"] = "rejected"
            s["rejected_reason"] = (
                "Close negative for untitled_match_as_identity: the reply "
                "reports that product 3's title field is blank and does not "
                "treat the untitled row as a confirmed catalog name. Boundary: "
                "disclose empty title ≠ use the fuzzy hit as the product the "
                "user named (0007/0008)."
            )
            rejected = s
            break
    (STATE / "student_suggestions.json").write_text(
        json.dumps(sugg, indent=2) + "\n"
    )

    for mode in patterns["modes"]:
        name = mode["name"]
        if name in extra:
            mode.update(extra[name])
            mode["any_instance_count"] = counts[name]
            negs = extra[name]["close_negative_scenario_ids"]
            mode["close_negative_trace_ids"] = []
            for sid in negs:
                mode["close_negative_trace_ids"].extend(by_sid.get(sid, []))

    patterns["reconciliation"] = {
        "traces_open_coded": 100,
        "review_set": 100,
        "label_cells": 800,
        "any_instance_total": sum(counts.values()),
        "holdout_new_consequential_modes": 0,
        "part_c_workshop": "skipped_optional",
    }
    patterns["taxonomy_revision"] = (
        "Stayed at 8 modes. Parked leftover_refund_unexplained, "
        "order_product_store_mismatch, escalate_without_ack, and "
        "unconfirmed_cancel (spec gap: confirm-then-cancel is not in SPEC)."
    )
    patterns["agentdebug_compare"] = (
        "AgentDebug (arXiv:2509.25370) uses generic agent failures "
        "(planning, tool-use, hallucination, instruction-following). "
        "Cartwheel modes are product-specific. invented_claim_not_in_tool "
        "maps to hallucination / tool-result misreport; untitled_match_as_identity "
        "to overconfident tool-use; reask_refund_reasons and disputes_with_refund "
        "to instruction-following. Parked unconfirmed_cancel is the nearest "
        "AgentDebug unconfirmed-write analogue; it was not added, to stay at 8 "
        "and because SPEC does not require a confirm turn on an imperative cancel."
    )
    patterns["rejected_suggestion"] = {
        "id": "sugg-66-support-0006",
        "scenario_id": "support-0006",
        "mode": "untitled_match_as_identity",
        "decision": "rejected",
        "reason": (rejected or {}).get("rejected_reason"),
    }
    (STATE / "student_patterns.json").write_text(
        json.dumps(patterns, indent=2) + "\n"
    )

    print("review set", len(rows))
    print("cells", 800)
    for m in MODES:
        n = counts[m]
        print(f"  {m}: {n}/100 = {n/100:.2f}")
    print("rejected", (rejected or {}).get("id"))


if __name__ == "__main__":
    main()
