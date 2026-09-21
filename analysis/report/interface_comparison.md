# Review interface comparison

Homework 4 asks for one design kept from the reference app, one change driven by these traces, and one remaining limitation.

## Kept from the reference

Role color on the left border, tool-call + tool-result in one step, collapsed long JSON, and margin notes with accept/dismiss are unchanged. That encoding is how you read a Cartwheel turn: the bug is often in the tool result, not the prose.

## Changed for these traces

1. **Submission path.** The HW4 app lives in `analysis/review_app/`, not the unchanged `analysis/ui/index.html`. Run `python analysis/review_app/server.py`.
2. **Conversation grouping.** Langfuse stores one trace per user turn. The Trace view concatenates turns that share `session_id`, or `cartwheel.scenario_id` when Langfuse `sessionId` is empty (true of the Homework 3 export). Five scenarios have two turns (`support-0113`, `0127`, `0066`, `0061`, `0051`).
3. **Header shows `scenario_id`, role, and intent.** Langfuse hid `cartwheel.scenario_id` in span attributes; that was the review friction from the standard annotation view.
4. **Tool-result chips** for `refund_eligible: false` and empty `title`.
5. **Progress reports incomplete present/absent cells** on the open-coded traces, plus the suggestion queue.

## Still limited

Langfuse `sessionId` is null on every exported trace, so grouping uses scenario id as the conversation key. The map is two feature axes (tool calls × turns), not PCA/UMAP. Langfuse score writes run only when `LANGFUSE_*` is set and a structured 0/1 label exists.
