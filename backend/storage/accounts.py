"""Accounts and profiles (fastapi-backend Phase 1): register, sign in, sign out, read and change
the profile.

Every read and change is by the signed-in person's own id. Passwords are hashed with scrypt
(core/security.py) and never stored or logged. Audit events say what happened (and which consent
was switched on or off), never the login, the password, the name or the place.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from ..core.security import burn_password_check, hash_password, verify_password
from ..errors import AppException, ForbiddenException, NotFoundException, ValidationException
from ..errors.error_codes import ErrorCode
from ..errors.exceptions import ConflictException
from .models import Profile, User
from .repository import audit

LANGUAGES = ("en", "hi", "mr", "bn", "ta", "te", "gu", "kn", "ml", "pa", "or", "ur")
ROLES = ("senior", "family")
CONSENTS = ("use_llm", "medical_cloud", "cloud_ocr")
ACCESSIBILITY_DEFAULTS = {"font_scale": 1.0, "high_contrast": False, "voice": False}
MOBILE = re.compile(r"[6-9]\d{9}")  # an Indian mobile number
EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+")
PHONE_PUNCTUATION = re.compile(r"[\s\-().]")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
LOGIN_HELP = "Enter a 10-digit mobile number or an e-mail address"
MIN_PASSWORD, MAX_PASSWORD = 8, 128


class InvalidCredentials(AppException):
    def __init__(self) -> None:
        super().__init__(message="The login or password is wrong", error_code=ErrorCode.INVALID_CREDENTIALS,
                         status_code=401)


class LoginTaken(ConflictException):
    def __init__(self) -> None:
        super().__init__(message="An account with this login already exists", error_code=ErrorCode.LOGIN_TAKEN)


def normalize_login(text: str) -> str:
    """A mobile number as +91XXXXXXXXXX (spaces, dashes, +91, 91 or 0 in front allowed), or an
    e-mail address in lower case. Anything else is refused."""
    raw = (text or "").strip()
    if "@" in raw:
        email = raw.lower()
        if len(email) <= 254 and EMAIL.fullmatch(email):
            return email
        raise ValidationException(LOGIN_HELP)
    digits = PHONE_PUNCTUATION.sub("", raw)
    if digits.startswith("+91"):
        digits = digits[3:]
    elif len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if MOBILE.fullmatch(digits):
        return "+91" + digits
    raise ValidationException(LOGIN_HELP)


def clean_name(text: str) -> str:
    name = " ".join(CONTROL.sub("", text or "").split())
    if not 1 <= len(name) <= 120:
        raise ValidationException("The name must be 1 to 120 characters")
    return name


def now() -> datetime:
    return datetime.now(timezone.utc)


def user_view(user: User) -> dict[str, Any]:
    return {"user_id": user.id, "login": user.login, "display_name": user.display_name, "role": user.role}


def profile_view(profile: Profile) -> dict[str, Any]:
    home = None
    if profile.home_lat is not None and profile.home_lon is not None:
        home = {"label": profile.home_label, "lat": profile.home_lat, "lon": profile.home_lon}
    stored_consents = profile.consents or {}
    return {
        "language": profile.language,
        "home_location": home,
        "accessibility": {**ACCESSIBILITY_DEFAULTS, **(profile.accessibility or {})},
        "consents": {name: bool(stored_consents.get(name, False)) for name in CONSENTS},
        "consents_updated_at": profile.consents_updated_at,
    }


class AccountService:
    def __init__(self, sessions: sessionmaker):
        self.sessions = sessions

    # ---- accounts ----------------------------------------------------------------------------

    def register(self, login: str, password: str, display_name: str, role: str = "senior") -> dict[str, Any]:
        if role not in ROLES:
            raise ValidationException(f"role must be one of: {', '.join(ROLES)}")
        if not MIN_PASSWORD <= len(password or "") <= MAX_PASSWORD:
            raise ValidationException(f"The password must be {MIN_PASSWORD} to {MAX_PASSWORD} characters")
        user = User(id=str(uuid4()), login=normalize_login(login), password_hash=hash_password(password),
                    display_name=clean_name(display_name), role=role, token_version=0, disabled=False)
        try:
            with self.sessions.begin() as session:
                session.add(user)
                session.add(Profile(user_id=user.id, language="en", accessibility={}, consents={}))
                audit(session, user.id, "account.registered", None, role=role)
        except IntegrityError:
            raise LoginTaken() from None
        return user_view(user)

    def sign_in(self, login: str, password: str) -> dict[str, Any]:
        """The user (with token_version, for the token) if the password is right. A login that
        matches no account takes as long as a wrong password, and gives the same answer."""
        try:
            normalized = normalize_login(login)
        except ValidationException:
            burn_password_check(password)
            raise InvalidCredentials() from None
        with self.sessions.begin() as session:
            user = session.execute(select(User).where(User.login == normalized)).scalar_one_or_none()
            if user is None:
                burn_password_check(password)
                raise InvalidCredentials()
            right = verify_password(password, user.password_hash)
            if not right:
                audit(session, user.id, "account.sign_in_failed", None)  # kept: the error is raised below
            elif not user.disabled:
                user.last_login_at = now()
                audit(session, user.id, "account.signed_in", None)
            signed_in = {**user_view(user), "token_version": user.token_version}
            disabled = user.disabled
        if not right:
            raise InvalidCredentials()
        if disabled:  # only someone with the right password learns this
            raise ForbiddenException("This account is switched off")
        return signed_in

    def sign_out(self, user_id: str) -> None:
        """Every token issued so far stops working."""
        with self.sessions.begin() as session:
            result = session.execute(update(User).where(User.id == user_id)
                                     .values(token_version=User.token_version + 1))
            if result.rowcount != 1:
                raise NotFoundException("There is no account for this id")
            audit(session, user_id, "account.signed_out", None)

    def user(self, user_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            user = session.get(User, user_id)
        if user is None:
            raise NotFoundException("There is no account for this id")
        return user_view(user)

    def saved_choices(self, user_id: str) -> dict[str, Any]:
        """For a request: the name (hidden from the model) and the saved consents. No account
        (development, X-Actor-Id): no name, every consent off."""
        with self.sessions() as session:
            user, profile = session.get(User, user_id), session.get(Profile, user_id)
        consents = profile_view(profile)["consents"] if profile else {name: False for name in CONSENTS}
        return {"display_name": user.display_name if user else None, "consents": consents}

    def token_valid(self, user_id: str, token_version: int) -> bool:
        """The account still exists, is not switched off, and has not signed out since."""
        with self.sessions() as session:
            row = session.execute(select(User.token_version, User.disabled).where(User.id == user_id)).first()
        return row is not None and not row.disabled and row.token_version == token_version

    # ---- profile -----------------------------------------------------------------------------

    def _profile(self, session: Any, user_id: str) -> Profile:
        profile = session.get(Profile, user_id)
        if profile is not None:
            return profile
        if session.get(User, user_id) is None:
            raise NotFoundException("There is no account for this id")
        profile = Profile(user_id=user_id, language="en", accessibility={}, consents={})
        session.add(profile)
        return profile

    def profile(self, user_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            return profile_view(self._profile(session, user_id))

    def update_profile(self, user_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """changes holds only what is being changed:
        - language: one of LANGUAGES;
        - home: None (forget it) or {"label", "lat", "lon"}, already checked and rounded;
        - accessibility: some of font_scale, high_contrast, voice;
        - consents: some of use_llm, medical_cloud, cloud_ocr (true or false)."""
        with self.sessions.begin() as session:
            profile = self._profile(session, user_id)
            if "language" in changes:
                if changes["language"] not in LANGUAGES:
                    raise ValidationException(f"language must be one of: {', '.join(LANGUAGES)}")
                profile.language = changes["language"]
            if "home" in changes:
                home = changes["home"] or {}
                profile.home_label, profile.home_lat, profile.home_lon = (home.get("label"), home.get("lat"),
                                                                          home.get("lon"))
            if changes.get("accessibility"):
                unknown = set(changes["accessibility"]) - set(ACCESSIBILITY_DEFAULTS)
                if unknown:
                    raise ValidationException("Unknown accessibility setting")
                profile.accessibility = {**(profile.accessibility or {}), **changes["accessibility"]}
            if changes.get("consents"):
                given = changes["consents"]
                if set(given) - set(CONSENTS) or not all(isinstance(value, bool) for value in given.values()):
                    raise ValidationException(f"consents are true or false for: {', '.join(CONSENTS)}")
                before = profile_view(profile)["consents"]
                after = {**before, **given}
                if after != before:
                    profile.consents = after
                    profile.consents_updated_at = now()
                    audit(session, user_id, "profile.consents_changed", None, **after)
            audit(session, user_id, "profile.updated", None, fields=sorted(changes))
            session.flush()
            return profile_view(profile)
