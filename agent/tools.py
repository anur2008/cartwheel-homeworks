"""Homework 1: the remaining commerce-agent tools.

The three lecture tools (`search_help_center`, `get_order`, `issue_refund`)
are implemented in agent/agent.py and are worked examples of the pattern:
check permissions first, go through agent/db.py for data, and return a
structured dict, never a prose error. Your four tools follow the same
pattern. agent/agent.py already wraps each function below as an SDK tool, so
once a function works here it works in chat with no further wiring.

Result convention (see agent/auth.py):
  - Success: a dict with "ok": True plus the payload fields named in each
    docstring.
  - Failure: {"ok": False, "error": <code>, "reason": <human-readable str>}.

Run the contract tests with: uv run pytest tests/test_hw_holes.py -k hw1
They are marked xfail and flip to passing as you implement each function.
"""

from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz

from agent import db
from agent.auth import AuthContext, can_cancel_order, can_view_order, permission_denied
from agent.config import load_facts
from agent.helpcenter import load_policy_docs
from agent.killswitch import kill_switch
from seed.eligibility import (
    effective_return_window_days,
    is_refund_eligible,
    refund_needs_approval,
)

MAX_SEARCH_LIMIT = 25
DEFAULT_ORDER_LIMIT = 20
FIND_ORDER_SCAN_LIMIT = 10_000
FIND_ORDER_RESULT_LIMIT = 5
FIND_ORDER_MIN_SCORE = 70


def get_policy(ctx: AuthContext, policy_id: str) -> dict[str, Any]:
    """Fetch one policy doc by its exact id. Risk tier: read.

    Every role may read every policy doc (the corpus is public help-center
    content), so this tool needs no permission check.

    Args:
        ctx: The caller's auth context. Unused here, but every tool takes it.
        policy_id: An exact policy id, e.g. "cw-returns" or
            "store-juniper-home-goods-policy". Matching is exact and
            case-sensitive; ids are the `policy_id` front-matter field of the
            files in data/policies/.

    Returns:
        On success: {"ok": True, "policy_id": str, "title": str,
        "audience": str, "body": str} where body is the markdown body of the
        doc without the front matter.
        If no doc has that id: {"ok": False, "error": "not_found",
        "reason": ...} naming the id that was requested.

    Implementation notes:
        agent.helpcenter.load_policy_docs() returns every parsed doc.
    """
    for doc in load_policy_docs():
        if doc.policy_id == policy_id:
            return {
                "ok": True,
                "policy_id": doc.policy_id,
                "title": doc.title,
                "audience": doc.audience,
                "body": doc.body,
            }
    return {
        "ok": False,
        "error": "not_found",
        "reason": f"no policy with id {policy_id!r}",
    }


def search_products(
    ctx: AuthContext,
    query: str,
    store: str | None = None,
    max_price_usd: float | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Search the product catalog. Risk tier: read.

    Every role may search products. Matching is deterministic keyword
    matching, not semantic search: a product matches when every whitespace
    token of `query` appears case-insensitively as a substring of the
    product's title or description.

    Args:
        ctx: The caller's auth context.
        query: Free-text query. Must be non-empty after stripping whitespace;
            otherwise return {"ok": False, "error": "invalid_argument",
            "reason": ...}.
        store: Optional store filter. Matched with
            agent.db.get_store_by_name (case-insensitive name or slug). If
            given and no store matches, return {"ok": False, "error":
            "not_found", "reason": ...} naming the store string.
        max_price_usd: Optional inclusive price ceiling. If given and not
            strictly positive, return an "invalid_argument" error.
        limit: Maximum products to return. Clamp to the range
            [1, MAX_SEARCH_LIMIT]; do not error on out-of-range values.

    Returns:
        {"ok": True, "products": [...], "count": <len(products)>} where each
        product is {"product_id": int, "store_id": int, "title": str,
        "price_usd": float}. Sort matches by price_usd ascending, then by
        product_id ascending, and truncate to `limit`. No matches is still a
        success: {"ok": True, "products": [], "count": 0}.

    Implementation notes:
        agent.db.list_products(conn, store_id) gives the candidate set.
        Open the database with agent.db.connect() and close it when done.
    """
    query = query.strip()
    if not query:
        return {
            "ok": False,
            "error": "invalid_argument",
            "reason": "query must be non-empty",
        }
    if max_price_usd is not None and max_price_usd <= 0:
        return {
            "ok": False,
            "error": "invalid_argument",
            "reason": "max_price_usd must be strictly positive",
        }
    clamped_limit = max(1, min(limit, MAX_SEARCH_LIMIT))
    tokens = query.lower().split()
    conn = db.connect()
    try:
        store_id = None
        if store is not None:
            matched = db.get_store_by_name(conn, store)
            if matched is None:
                return {
                    "ok": False,
                    "error": "not_found",
                    "reason": f"no store matching {store!r}",
                }
            store_id = matched.id
        matches: list[dict[str, Any]] = []
        for product in db.list_products(conn, store_id):
            haystack = f"{product.title} {product.description}".lower()
            if not all(token in haystack for token in tokens):
                continue
            if max_price_usd is not None and product.price_usd > max_price_usd:
                continue
            matches.append(
                {
                    "product_id": product.id,
                    "store_id": product.store_id,
                    "title": product.title,
                    "price_usd": product.price_usd,
                }
            )
        matches.sort(key=lambda p: (p["price_usd"], p["product_id"]))
        products = matches[:clamped_limit]
        return {"ok": True, "products": products, "count": len(products)}
    finally:
        conn.close()


def list_my_orders(ctx: AuthContext) -> dict[str, Any]:
    """List recent orders in the caller's own scope. Risk tier: read.

    Role behavior, straight from the access matrix in SPEC.md:
        - shopper: the caller's own orders.
        - merchant: the caller's store's orders (ctx.store_id).
        - support: support staff have no orders of their own and look up
          specific orders with get_order instead, so return {"ok": False,
          "error": "invalid_argument", "reason": ...} saying exactly that.

    Returns:
        For shopper and merchant: {"ok": True, "orders": [...],
        "count": <len(orders)>} where each order is
        agent.db.Order.to_public_dict() and the list holds at most
        DEFAULT_ORDER_LIMIT orders, newest first (agent.db.list_orders_for_user
        and list_orders_for_store already sort and limit this way).

    Implementation notes:
        No permission check is needed beyond the role dispatch, because the
        scope is baked into which query you run. That is the point of the
        tool: the model cannot ask for someone else's orders through it.
    """
    if ctx.role == "support":
        return {
            "ok": False,
            "error": "invalid_argument",
            "reason": (
                "support staff have no orders of their own and look up "
                "specific orders with get_order instead"
            ),
        }
    conn = db.connect()
    try:
        if ctx.role == "shopper":
            records = db.list_orders_for_user(
                conn, ctx.user_id, limit=DEFAULT_ORDER_LIMIT
            )
        else:
            records = db.list_orders_for_store(
                conn, ctx.store_id, limit=DEFAULT_ORDER_LIMIT
            )
        orders = [order.to_public_dict() for order in records]
        return {"ok": True, "orders": orders, "count": len(orders)}
    finally:
        conn.close()


def cancel_order(ctx: AuthContext, order_id: int, reason: str) -> dict[str, Any]:
    """Cancel an order. Risk tier: write.

    This is the homework's write tool, and it must enforce two independent
    rules in this order:

    1. The access matrix (scope): use agent.auth.can_cancel_order. Shoppers
       may cancel only their own orders, merchants only their own store's
       orders, support any order. On failure return
       agent.auth.permission_denied(...) with a reason naming the role and
       the order id. Scope is checked before the status rule so that an
       out-of-scope caller learns nothing about the order's state.
    2. The pre-shipment rule (facts.yaml `cancel_cutoff`): only orders whose
       status is exactly "placed" can be cancelled, for every role. If the
       order is in scope but its status is not "placed", return
       {"ok": False, "error": "not_eligible", "reason": ...} that names the
       current status and states that orders can be cancelled only before
       shipment.

    Args:
        ctx: The caller's auth context.
        order_id: The order to cancel.
        reason: Free-text reason from the user; not validated.

    Returns:
        If no order has this id: {"ok": False, "error": "not_found",
        "reason": ...}.
        On success: {"ok": True, "order_id": order_id, "status": "cancelled"}
        after persisting the new status with agent.db.set_order_status.

    Implementation notes:
        Fetch with agent.db.get_order. Note the argument order of
        can_cancel_order(ctx, order_user_id, order_store_id).

    The Module 4 kill switch is checked first (before the scope and
    status rules and before your code), so that a paused write tool touches
    nothing. It is provided; the default ("off") returns None and falls
    through to your implementation.
    """
    paused = kill_switch("cancel_order")
    if paused is not None:
        return {"ok": False, "error": "paused", "reason": paused}
    conn = db.connect()
    try:
        order = db.get_order(conn, order_id)
        if order is None:
            return {"ok": False, "error": "not_found", "reason": f"no order #{order_id}"}
        if not can_cancel_order(ctx, order.user_id, order.store_id):
            return permission_denied(
                f"role '{ctx.role}' (user {ctx.user_id}) may not cancel order #{order_id}"
            )
        if order.status != "placed":
            return {
                "ok": False,
                "error": "not_eligible",
                "reason": (
                    f"order #{order_id} has status '{order.status}'; "
                    "orders can be cancelled only before shipment"
                ),
            }
        db.set_order_status(conn, order_id, "cancelled")
        return {"ok": True, "order_id": order_id, "status": "cancelled"}
    finally:
        conn.close()


def find_order(ctx: AuthContext, query: str) -> dict[str, Any]:
    """Search the caller's orders by product name. Risk tier: read.

    Takes a natural-language query (e.g., "earmuffs I bought last week")
    and searches the authenticated user's orders for products whose name
    matches. Use fuzzy string matching (e.g., thefuzz.fuzz.partial_ratio
    or SQLite LIKE) to find orders whose product name is close to the
    query.

    Access rules: a shopper searches only the shopper's own orders, a
    merchant searches orders from the merchant's store, and support staff
    can search any orders. Use agent.db.list_orders_for_user for shoppers
    and agent.db.list_orders_for_store for merchants. For support staff,
    use agent.db.list_orders_for_user with no user filter, or search
    across all orders.

    Args:
        ctx: The caller's auth context.
        query: A natural-language description of the product.

    Returns:
        {"ok": True, "orders": [...]} with a list of matching orders
        (at most 5), each as the dict returned by agent.db. If no orders
        match, return {"ok": True, "orders": []}.
    """
    conn = db.connect()
    try:
        products_by_id = {product.id: product for product in db.list_products(conn)}
        if ctx.role == "shopper":
            candidates = db.list_orders_for_user(
                conn, ctx.user_id, limit=FIND_ORDER_SCAN_LIMIT
            )
        elif ctx.role == "merchant":
            candidates = db.list_orders_for_store(
                conn, ctx.store_id, limit=FIND_ORDER_SCAN_LIMIT
            )
        else:
            store_ids = {product.store_id for product in products_by_id.values()}
            candidates = []
            for store_id in store_ids:
                candidates.extend(
                    db.list_orders_for_store(
                        conn, store_id, limit=FIND_ORDER_SCAN_LIMIT
                    )
                )
        scored: list[tuple[float, db.Order]] = []
        needle = query.strip()
        for order in candidates:
            product = products_by_id.get(order.product_id)
            if product is None:
                continue
            score = fuzz.partial_ratio(needle.lower(), product.title.lower())
            if score >= FIND_ORDER_MIN_SCORE:
                scored.append((score, order))
        scored.sort(key=lambda pair: (-pair[0], -pair[1].id))
        orders = []
        for _, order in scored[:FIND_ORDER_RESULT_LIMIT]:
            payload = order.to_public_dict()
            payload["id"] = order.id
            orders.append(payload)
        return {"ok": True, "orders": orders}
    finally:
        conn.close()


def check_refund_eligibility(ctx: AuthContext, order_id: int) -> dict[str, Any]:
    """Explain whether an order can be returned or refunded. Risk tier: read.

    get_order already stamps a boolean refund_eligible flag. This tool
    recomputes that decision from the eligibility oracle and returns the
    window, store override, and reasons so the model does not invent dates
    or policy numbers.

    Access is the same as viewing an order: shoppers see only their own
    orders, merchants only their store's, support any order.

    Args:
        ctx: The caller's auth context.
        order_id: The order to check.

    Returns:
        On success: ok, order_id, eligible, status, window fields, and
        reasons. Unknown orders return not_found. Out-of-scope orders
        return permission_denied before any eligibility fields.
    """
    facts = load_facts()
    conn = db.connect()
    try:
        order = db.get_order(conn, order_id)
        if order is None:
            return {"ok": False, "error": "not_found", "reason": f"no order #{order_id}"}
        if not can_view_order(ctx, order.user_id, order.store_id):
            return permission_denied(
                f"role '{ctx.role}' (user {ctx.user_id}) may not view order #{order_id}"
            )
        store = db.get_store(conn, order.store_id)
        as_of = db.world_asof(conn)
        platform_window = int(facts["return_window_days"])
        store_override = store.return_window_days_override if store else None
        window = effective_return_window_days(platform_window, store_override)
        eligible = is_refund_eligible(
            status=order.status,
            delivered_at=order.delivered_at,
            as_of=as_of,
            return_window_days=window,
        )
        threshold = facts["refund_auto_approve_threshold_usd"]
        days_since_delivery = (
            (as_of - order.delivered_at).days if order.delivered_at is not None else None
        )
        days_remaining = (
            window - days_since_delivery if days_since_delivery is not None else None
        )
        reasons = _eligibility_reasons(
            order_id=order_id,
            status=order.status,
            eligible=eligible,
            store_name=store.name if store else None,
            platform_window=platform_window,
            store_override=store_override,
            window=window,
            days_since_delivery=days_since_delivery,
            days_remaining=days_remaining,
            total_usd=order.total_usd,
            threshold_usd=threshold,
        )
        return {
            "ok": True,
            "order_id": order_id,
            "eligible": eligible,
            "status": order.status,
            "delivered_at": (
                order.delivered_at.isoformat() if order.delivered_at else None
            ),
            "as_of": as_of.isoformat(),
            "return_window_days": window,
            "platform_return_window_days": platform_window,
            "store_override_days": store_override,
            "store_name": store.name if store else None,
            "days_since_delivery": days_since_delivery,
            "days_remaining": days_remaining,
            "max_refund_usd": order.total_usd,
            "auto_approve_threshold_usd": threshold,
            "full_refund_needs_approval": refund_needs_approval(
                order.total_usd, threshold
            ),
            "reasons": reasons,
        }
    finally:
        conn.close()


def _eligibility_reasons(
    *,
    order_id: int,
    status: str,
    eligible: bool,
    store_name: str | None,
    platform_window: int,
    store_override: int | None,
    window: int,
    days_since_delivery: int | None,
    days_remaining: int | None,
    total_usd: float,
    threshold_usd: float,
) -> list[str]:
    reasons: list[str] = []
    if store_override is not None:
        reasons.append(
            f"store {store_name!r} overrides the platform {platform_window}-day "
            f"window with a {store_override}-day window"
        )
    if status != "delivered":
        reasons.append(
            f"order #{order_id} has status '{status}'; only delivered orders "
            "can be returned or refunded"
        )
        if status == "placed":
            reasons.append("a placed order may still be cancelled before shipment")
        return reasons
    if days_since_delivery is None:
        reasons.append(f"order #{order_id} has no delivery date")
        return reasons
    if days_since_delivery < 0:
        reasons.append(f"order #{order_id} has a delivery date in the future")
        return reasons
    if eligible:
        reasons.append(
            f"delivered {days_since_delivery} days ago; {days_remaining} days "
            f"remain in the {window}-day return window"
        )
        if total_usd > threshold_usd:
            reasons.append(
                f"a refund of the ${total_usd:.2f} order total is above the "
                f"${threshold_usd} auto-approval threshold and would be queued "
                "for a human"
            )
        else:
            reasons.append(
                f"a refund of the ${total_usd:.2f} order total is at or below "
                f"the ${threshold_usd} auto-approval threshold"
            )
        return reasons
    reasons.append(
        f"delivered {days_since_delivery} days ago; the {window}-day return "
        "window has passed"
    )
    return reasons
