# Homework 5 summary: `refund_ineligible_without_why` judge

## Failure mode

A status or order-details reply says the order is not refund-eligible (or leaves out a false eligibility flag) without giving the reason: the return window or the store's shorter window. The neighboring mode, `reask_refund_reasons`, is judged separately, and the $100 auto-approval threshold does not count as a reason.

## Setup

- Judge model: `gpt-4o-mini`, used for both development and test.
- Inputs: 98 conversations in `analysis/state/hw5_trace_inputs.json`, with no labels, notes, or scenario metadata.
- Split (seed 7, 20/40/40): train 12 Pass / 8 Fail, development 24 Pass / 15 Fail, test 24 Pass / 15 Fail.
- Few-shot examples come from the train split only.

## Development

The first prompt (v0) was too strict. On cancel requests, the tool results still showed `refund_eligible: false`. The judge treated that leftover field as the failure, even though the agent had only confirmed the cancel. I had labeled those conversations Pass.

v1 tells the judge to first decide what the last reply is doing. A cancel is Pass, whatever the tool fields say. v2 added stronger wording for "not eligible, but the reason is given," but it did not change any development verdict, so I froze v1.

| Version | TP | FN | TN | FP | TPR (95% CI) | TNR (95% CI) |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| v0 | 20 | 4 | 15 | 0 | 0.83 (0.64–0.93) | 1.00 (0.80–1.00) |
| v1 | 22 | 2 | 15 | 0 | 0.92 (0.74–0.98) | 1.00 (0.80–1.00) |
| v2 | 22 | 2 | 15 | 0 | 0.92 (0.74–0.98) | 1.00 (0.80–1.00) |

I never edited my labels after Part A. The disagreements on development were fixed in the prompt, so development and test measure the same definition.

## Test (frozen v1, run once)

From `analysis/report/test-refund_ineligible_without_why-v1.json`:

| | Human Pass | Human Fail |
| --- | ---: | ---: |
| Judge Pass | TP 19 | FP 0 |
| Judge Fail | FN 5 | TN 15 |

- TPR = 19 / (19 + 5) = 19/24 = 0.79 (95% CI 0.60–0.91)
- TNR = 15 / (15 + 0) = 15/15 = 1.00 (95% CI 0.80–1.00)

## Would I use it

Yes, as a first-pass filter to flag conversations for human review. It did not miss any conversation I labeled Fail. I would not use it to label on its own, because it called Fail on 5 of my 24 Pass conversations (two "where is my order" questions, a shipped-order cancel, a chargeback reply that gave the 30-day reason, and an eligible refund). With only 39 test conversations, the intervals are wide.

## Video

Pending.
