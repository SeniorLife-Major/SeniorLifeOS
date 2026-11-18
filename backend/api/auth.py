"""Sign-in (fastapi-backend Phase 1): make an account, sign in, sign out, who am I.

The token from register or login goes in every later request as Authorization: Bearer <token>.
It lasts security.token_ttl_minutes (12 hours by default); signing out ends every token of that
account at once.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import Field

from backend.api.deps import Actor, SignInUnavailable, get_account_service
from backend.common.schemas import ApiResponse, CamelModel
from backend.core.config import get_config
from backend.core.security import TokenSecretError, create_access_token, token_secret

router = APIRouter(prefix="/api/v1/auth", tags=["Sign-in"])
logger = logging.getLogger(__name__)


# ---- DTOs ---------------------------------------------------------------------------------

class RegisterRequest(CamelModel):
    login: str = Field(max_length=254)  # a 10-digit mobile number or an e-mail address
    password: str = Field(max_length=128)  # 8 to 128 characters
    display_name: str = Field(max_length=120)
    role: Literal["senior", "family"] = "senior"


class SignInRequest(CamelModel):
    login: str = Field(max_length=254)
    password: str = Field(max_length=128)


class UserDto(CamelModel):
    user_id: str
    login: str
    display_name: str
    role: str


class SessionDto(CamelModel):
    user: UserDto
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class SignedOutDto(CamelModel):
    signed_out: bool = True


# ---- helpers ------------------------------------------------------------------------------

Accounts = Annotated[Any, Depends(get_account_service)]


def envelope(data: Any, response: Response) -> ApiResponse[Any]:
    response.headers["Cache-Control"] = "no-store"  # tokens and personal details
    return ApiResponse.of(data=data, version=get_config().app.version)


def check_secret() -> Any:
    """The config, if tokens can be made. Checked before an account is made, so a server with
    an unusable JWT_SECRET never makes an account it can't sign in."""
    config = get_config()
    try:
        token_secret(config)
    except TokenSecretError as exc:
        logger.error("Sign-in tokens can't be made: %s", exc)
        raise SignInUnavailable() from None
    return config


def session_for(user: dict[str, Any], config: Any) -> SessionDto:
    token, seconds = create_access_token(user["user_id"], user["token_version"], config)
    return SessionDto(user=UserDto.model_validate(user), access_token=token, expires_in=seconds)


# ---- routes -------------------------------------------------------------------------------

@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=ApiResponse[SessionDto],
             summary="Make an account and sign in")
def register(body: RegisterRequest, response: Response, accounts: Accounts) -> ApiResponse[SessionDto]:
    config = check_secret()
    user = accounts.register(body.login, body.password, body.display_name, body.role)
    return envelope(session_for({**user, "token_version": 0}, config), response)


@router.post("/login", response_model=ApiResponse[SessionDto], summary="Sign in with a mobile number or e-mail")
def login(body: SignInRequest, response: Response, accounts: Accounts) -> ApiResponse[SessionDto]:
    config = check_secret()
    return envelope(session_for(accounts.sign_in(body.login, body.password), config), response)


@router.post("/logout", response_model=ApiResponse[SignedOutDto], summary="Sign out everywhere")
def logout(response: Response, actor: Actor, accounts: Accounts) -> ApiResponse[SignedOutDto]:
    accounts.sign_out(actor)
    return envelope(SignedOutDto(), response)


@router.get("/me", response_model=ApiResponse[UserDto], summary="Who is signed in")
def me(response: Response, actor: Actor, accounts: Accounts) -> ApiResponse[UserDto]:
    return envelope(UserDto.model_validate(accounts.user(actor)), response)
