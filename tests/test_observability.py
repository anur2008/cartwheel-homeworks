"""Homework 2 authentication tests. Offline: no Langfuse, Docker, or model key."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from server import app as server_app


@pytest.mark.parametrize(
    "user_id, claimed_role, status",
    [
        (9002, "shopper", 403),
        (1, "merchant", 403),
        (1, "admin", 400),
        (999999, "shopper", 404),
    ],
)
def test_create_session_rejects_invalid_identity(
    world: dict, user_id: int, claimed_role: str, status: int
) -> None:
    server_app._SESSIONS.clear()
    with pytest.raises(HTTPException) as exc_info:
        server_app.create_session(
            server_app.SessionCreate(user_id=user_id, role=claimed_role)
        )
    assert exc_info.value.status_code == status


def test_token_cannot_authorize_a_different_session(world: dict) -> None:
    server_app._SESSIONS.clear()
    first = server_app.create_session(
        server_app.SessionCreate(user_id=1, role="shopper")
    )
    second = server_app.create_session(
        server_app.SessionCreate(user_id=9002, role="merchant")
    )
    with pytest.raises(HTTPException) as exc_info:
        server_app._authorize(
            second["session_id"], f"Bearer {first['token']}"
        )
    assert exc_info.value.status_code == 403


@pytest.mark.parametrize(
    "authorization, status",
    [
        (None, 401),
        ("Bearer not-a-real-token", 401),
    ],
)
def test_authorize_rejects_missing_or_invalid_token(
    world: dict, authorization: str | None, status: int
) -> None:
    server_app._SESSIONS.clear()
    session = server_app.create_session(
        server_app.SessionCreate(user_id=1, role="shopper")
    )
    with pytest.raises(HTTPException) as exc_info:
        server_app._authorize(session["session_id"], authorization)
    assert exc_info.value.status_code == status


def test_authorize_rejects_unknown_session(world: dict) -> None:
    server_app._SESSIONS.clear()
    session = server_app.create_session(
        server_app.SessionCreate(user_id=1, role="shopper")
    )
    server_app._SESSIONS.clear()
    with pytest.raises(HTTPException) as exc_info:
        server_app._authorize(
            session["session_id"], f"Bearer {session['token']}"
        )
    assert exc_info.value.status_code == 404
