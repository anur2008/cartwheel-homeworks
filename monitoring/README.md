# Homework 7 monitoring results

## Setup

- Failure mode: `refund_ineligible_without_why`
- Frozen judge: `refund_ineligible_without_why-v1` on `gpt-4o-mini`, held-out test failure sensitivity 1.0 (15 of 15 failures flagged) and pass specificity 0.7917 (19 of 24 passes left unflagged)
- Cartwheel model: `claude-opus-4-6` in both periods
- Scenarios: the 50 in `scenarios/monitoring_scenarios.jsonl`
- Random sample: 20 percent (10 conversations) per period, seed 7, so both periods judged the same 10 scenarios
- Risk group: `write_action` (conversations that called `issue_refund` or `cancel_order`)
- Threshold: corrected failure rate 0.15, chosen before either period was judged

| Period | Window (UTC) | Langfuse traces | Conversations | Random flagged | Raw | Corrected | 95% interval | Write-action flagged |
|---|---|---|---|---|---|---|---|---|
| before (HW3 run) | 2026-09-14 17:33:45 to 17:57:56 | 50 | 50 | 2 of 10 | 0.20 | 0.00 | 0.00 to 0.35 | 0 of 2 |
| after (new run) | 2026-10-04 05:34:42 to 05:43:42 | 51 | 50 | 3 of 10 | 0.30 | 0.12 | 0.00 to 0.51 | 0 of 2 |

Flagged random-sample conversations: support-0076 and support-0085 in both periods, support-0117 in the after period only.

Between the periods, the endpoint began recording `cartwheel.session_id` and hashing only the prompt template for `cartwheel.prompt_version`, as its contract requires. These change trace metadata only, not the agent's prompt, tools, or replies.

## 1. Did the corrected failure estimate move between the two periods?

Yes from 0 to 0.12. May be due to support-0117 which was judged as a failure in this month (oct) run?

## 2. Do the intervals support a conclusion, or is the result uncertain?

with just 10 sample scenarios evaluated by the judge not able to conclude anything solid. May be the flagged scenarios in both runs can help investigate the point of failure

## 3. What did the risk groups reveal that the random estimate did not?
Knowing that targeted sampling finds problems but can't measure rates.
## 4. What action should happen if the estimate crosses the threshold?
Flagged traces should be investigated and should be run as part of regression before every release
