# Homework 4 review summary

Reviewed sample: **100 distinct traces** from the Homework 3 Cartwheel export (`traces/support_traces.json`). Open codes are in `analysis/state/student_annotations.json`. Structured present/absent labels are in `analysis/state/student_labels.json` and `analysis/state/labels/<mode>.jsonl` (800 cells). The demo `unsupported_policy_claim.jsonl` was left unchanged.

These are **sample fractions**, not prevalence. Clustering, role quotas, and depth search changed the mix. Homework 5 estimates prevalence on the full Module 1 store.

Langfuse scores were **not** written: `LANGFUSE_*` is not configured in this environment. Local jsonl is the matching record.

Part C (Raindrop Workshop) was skipped as optional. See `workshop_notes.md`.

## Sample composition

Four exclusive Part B batches (`analysis/state/sample_manifest.json`):

| Batch | n | Method |
| --- | ---: | --- |
| Cluster representatives | 15 | k-means on turn / tool / retrieval / token features |
| Uniform random | 15 | `selection.select`, seed 7 |
| Role-stratified | 30 | `cartwheel.user_role`, 10 shopper / 10 merchant / 10 support, seed 11 |
| Depth + close negatives | 25 | retrieval for named modes and no-miss neighbors |
| Holdout (after freeze) | 15 | uniform random, seed 13 |

Overall role mix after all four batches: shopper 56, merchant 23, support 21. At selection time, 44 of the 100 already had an open code.

## Taxonomy (8 binary modes)

Each mode has a binary definition, ≥3 confirmed positives from open coding, ≥3 close negatives in the review set, a neighbor boundary, an evaluator type, and a SPEC / policy source. Full fields live in `analysis/state/student_patterns.json`.

| Mode | Present / 100 | Sample fraction | Evaluator |
| --- | ---: | ---: | --- |
| `refund_ineligible_without_why` | 38 | 0.38 | judge |
| `reask_refund_reasons` | 10 | 0.10 | judge |
| `disputes_with_refund` | 2 | 0.02 | judge |
| `omit_product_title` | 4 | 0.04 | code |
| `escalate_offered_not_done` | 3 | 0.03 | code |
| `unnecessary_escalate_offer` | 4 | 0.04 | judge |
| `untitled_match_as_identity` | 3 | 0.03 | judge |
| `invented_claim_not_in_tool` | 3 | 0.03 | judge |

`disputes_with_refund` has three confirmed positives including `support-0047` outside this 100; the sample fraction on the review set is 2/100.

A trace may carry more than one mode. Example: `support-0013` is present for both `escalate_offered_not_done` and `invented_claim_not_in_tool` (RESP-3 sign-flipped price, then no ticket).

## Holdout stability

The final 15 traces produced **0 previously unseen consequential modes**. Existing modes did fire (`untitled_match_as_identity` on `support-0007`, several refund-why and reask traces). Taxonomy stayed at eight.

## Taxonomy revision

Stayed at eight modes. Parked rather than added:

- leftover “why” after a later eligibility call (`support-0074` / `0075`)
- store / product mismatch (`support-0028` / `0029`)
- escalate-without-ack (`support-0020` / `0017`)
- `unconfirmed_cancel` (`support-0148` / `0139` / `0140`)

`unconfirmed_cancel` is a **spec gap**, not a labeled failure: SPEC does not require a confirm turn on an imperative cancel. No `SPEC.md` edit was made.

Product wishes that were **not** labeled present: `$100` as an ineligibility reason (it is the auto-approve threshold), and asking `get_order` to return a product title (`support-0112` is a close negative for `omit_product_title`).

## Rejected search suggestion

`sugg-66-support-0006` (`untitled_match_as_identity`) was **rejected**. The reply reports that product 3’s title field is blank. That is a close negative: disclosing an empty title is not treating the untitled row as the product the user named (`support-0007` / `0008` are present).

## AgentDebug comparison

[AgentDebug](https://arxiv.org/abs/2509.25370) names generic agent failures (planning, tool-use, hallucination, instruction-following). Cartwheel modes are product-specific. Rough mapping:

- `invented_claim_not_in_tool` → hallucination / tool-result misreport
- `untitled_match_as_identity` → overconfident tool-use
- `reask_refund_reasons`, `disputes_with_refund` → instruction-following

Parked `unconfirmed_cancel` is the nearest AgentDebug unconfirmed-write analogue. It was not added, to stay at eight modes and because SPEC does not require a confirm turn.

## SPEC relationship (example)

`refund_ineligible_without_why` is present when a status or details reply reports ineligible (or omits the flag) without the return-window or store-override reason. Requirement source: agent prompt (“use `check_refund_eligibility` to explain why”) and SPEC RESP-1. Close negatives: `support-0071` (window stated), `support-0242` (eligible), `support-0180` (eligibility Yes). Neighbor: `reask_refund_reasons` is the write-path Q&A, not a missing why on status.

## Homework 5 note

Several modes have far fewer than 30 present labels in this sample (`disputes_with_refund` 2, several others 3–4). Synthetic targeting will be needed before judge split/validation.
