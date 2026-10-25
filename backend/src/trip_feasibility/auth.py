"""Who is calling (PRD ADR-5: Auth.js signs in, FastAPI verifies).

Auth.js owns sign-in (GitHub, Google, email links) in the Next.js server. For each API call,
that server signs a short-lived JWT with API_JWT_SECRET and sends it as
`Authorization: Bearer <token>`:

    {"sub": "<Auth.js user id>", "email": "...", "name": "...", "provider": "github",
     "iss": API_JWT_ISSUER, "aud": API_JWT_AUDIENCE, "iat": ..., "exp": ...}

Only HS256 is accepted, `exp`, `iat`, `iss`, `aud` and `email` are required, and a token may
live at most MAX_TOKEN_LIFETIME_S. Why not verify Auth.js's own session cookie: it's an
encrypted JWE whose format is an Auth.js internal; a token made for the API keeps FastAPI
independent of it and works for any caller holding the secret (tests, scripts).

The user row is created on first sight, keyed by email.
"""

import uuid
from typing import Annotated, Any

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from trip_feasibility.config import get_settings
from trip_feasibility.db import get_session
from trip_feasibility.errors import AppError
from trip_feasibility.models import User

MAX_TOKEN_LIFETIME_S = 3600
LEEWAY_S = 30


def _unauthorized(message: str) -> AppError:
    return AppError(401, "unauthorized", message)


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    if settings.api_jwt_secret is None:
        raise AppError(503, "unavailable", "Sign-in isn't configured on this server")
    try:
        claims = jwt.decode(
            token,
            settings.api_jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            audience=settings.api_jwt_audience,
            issuer=settings.api_jwt_issuer,
            leeway=LEEWAY_S,
            options={"require": ["exp", "iat", "iss", "aud", "email"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise _unauthorized("Session token expired") from e
    except jwt.InvalidTokenError as e:
        raise _unauthorized(f"Invalid session token: {e}") from e
    if claims["exp"] - claims["iat"] > MAX_TOKEN_LIFETIME_S:
        raise _unauthorized("Session token lives too long")
    if not isinstance(claims["email"], str) or "@" not in claims["email"]:
        raise _unauthorized("Session token has no valid email")
    return claims


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header is None:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized("Expected 'Authorization: Bearer <token>'")
    return token.strip()


async def _user_for(session: AsyncSession, claims: dict[str, Any]) -> User:
    email = claims["email"].strip().lower()
    stmt = (
        insert(User)
        .values(
            id=uuid.uuid4(),
            email=email,
            name=(claims.get("name") or None),
            auth_provider=str(claims.get("provider") or "unknown")[:50],
        )
        .on_conflict_do_nothing(index_elements=[User.email])
    )
    await session.execute(stmt)
    await session.commit()
    return (await session.execute(select(User).where(User.email == email))).scalar_one()


async def optional_user(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> User | None:
    """The caller, or None without an Authorization header. A bad token is still a 401:
    it never silently becomes an anonymous (demo) call."""
    token = _bearer(request)
    if token is None:
        return None
    return await _user_for(session, decode_token(token))


async def current_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
    if user is None:
        if get_settings().api_jwt_secret is None:
            raise AppError(503, "unavailable", "Sign-in isn't configured on this server")
        raise _unauthorized("Sign in to do this")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
OptionalUser = Annotated[User | None, Depends(optional_user)]
