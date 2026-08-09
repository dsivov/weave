"""Token issue and verify — and the one secret everything else rests on.

The JWT signing secret is the root of the whole trust model: a token carries the
`role` claim that RBAC enforces against, so whoever can sign a token can *be*
anybody. A6 requires the principal to come from the authenticated identity, and
a forged token is, by construction, an authenticated identity.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

import jwt
from dotenv import load_dotenv
from fastapi import HTTPException, status
from pydantic import BaseModel

from weave.server.config import global_args

# use the .env that is inside the current folder
# allows to use different .env file for each weave_core instance
# the OS environment variables take precedence over the .env file
load_dotenv(dotenv_path=".env", override=False)


#: The shipped placeholder. It is written in this file, in the repository, on the
#: internet — so it is not a secret, it is a published constant that happens to
#: sit where a secret belongs.
DEFAULT_TOKEN_SECRET = "weave_core-jwt-default-secret"

#: The only way to run on the placeholder: say so, explicitly, per process.
INSECURE_OVERRIDE_VAR = "WEAVE_ALLOW_INSECURE_JWT_SECRET"


class InsecureSigningSecret(RuntimeError):
    """Raised at startup when the signing secret is the published default."""


def assert_signing_secret_is_safe(
    secret: str, env: Optional[dict] = None
) -> None:
    """Refuse to start on the published default secret (S1, A6, A14).

    The server used to *warn* about this and carry on. A warning that can be
    ignored is not a control — and this one was worse than ignorable: until the
    M0 review it named the wrong environment variable, so an operator who acted
    on it changed nothing and had every reason to believe they were done.

    Before P1 the exposure was theoretical: nothing was deployed and there were
    no users to impersonate. With a persisted user store it is a complete RBAC
    bypass, because anybody who has read this repository can mint a token
    claiming any role in any workspace.

    The escape hatch is deliberately loud and deliberately per-process: an
    environment variable that has to be set on purpose, never a config default,
    so it cannot be inherited from a template someone copied for production.
    """
    env = os.environ if env is None else env
    if secret != DEFAULT_TOKEN_SECRET:
        return
    if str(env.get(INSECURE_OVERRIDE_VAR, "")).strip().lower() in {"1", "true", "yes", "on"}:
        return
    raise InsecureSigningSecret(
        "Refusing to start: WEAVE_TOKEN_SECRET is still the published default.\n"
        "\n"
        "  Every token this server issues carries the role RBAC enforces against,\n"
        "  so anyone who has read this repository could sign one claiming any role.\n"
        "\n"
        "  Set a real secret:\n"
        f"      export WEAVE_TOKEN_SECRET='{secrets.token_urlsafe(48)}'\n"
        "\n"
        "  (that value was generated just now, for you — or use any 32+ byte random string)\n"
        "\n"
        f"  For local development only, you may instead set {INSECURE_OVERRIDE_VAR}=true.\n"
        "  Do not put that in a deployment template."
    )


class TokenPayload(BaseModel):
    sub: str  # Username
    exp: datetime  # Expiration time
    role: str = "user"  # User role, default is regular user
    metadata: dict = {}  # Additional metadata


class AuthHandler:
    """Issues and validates tokens; the account source is the user store (A14).

    The source parsed accounts out of an environment string once, at import.
    Now they come from :class:`weave.server.users.UserService`, which is bound
    at application startup. Two consequences worth stating:

    * ``auth_configured`` is a **live** question, not a boot-time snapshot.
      An admin creating the first user from the UI must take effect on the next
      request, or "add a user without restarting" is not true (R13, R39).
    * the role still comes from the server side of the wire, never from the
      client — now from the user's stored record rather than a second
      environment variable (A6, R15).
    """

    def __init__(self):
        self.secret = global_args.token_secret
        self.algorithm = global_args.jwt_algorithm
        self.expire_hours = global_args.token_expire_hours
        self.guest_expire_hours = global_args.guest_token_expire_hours
        self._users = None

    # -- binding ------------------------------------------------------------

    def bind_user_service(self, service) -> None:
        """Attach the store. Called once, at application startup."""
        self._users = service

    @property
    def users(self):
        return self._users

    @property
    def auth_configured(self) -> bool:
        """Whether anyone can sign in. Asked per request, answered from the store."""
        if self._users is None:
            return False
        return self._users.any_user_exists

    # -- credentials --------------------------------------------------------

    def authenticate(self, username: str, password: str):
        """Return the authenticated :class:`~weave.server.users.User`, or None."""
        if self._users is None:
            return None
        user = self._users.authenticate(username, password)
        if user is not None:
            self._users.record_login(user)
        return user

    def role_for(self, username: str) -> str:
        """The role this account logs in as.

        Governance asks what someone *is* — a workspace grants `architect` the
        right to steer a fleet, not "whoever is signed in". Logging everyone in
        as a generic `user` left boards read-only for real people while service
        tokens minted their own roles, so the role is resolved here, on the
        server, and never taken from the client (A6: attribution is
        authenticated, not self-stamped).
        """
        if self._users is None:
            return "user"
        user = self._users.by_username(username)
        return user.role if user is not None else "user"

    def create_token(
        self,
        username: str,
        role: str = "user",
        custom_expire_hours: int = None,
        metadata: dict = None,
    ) -> str:
        """
        Create JWT token

        Args:
            username: Username
            role: User role, default is "user", guest is "guest"
            custom_expire_hours: Custom expiration time (hours), if None use default value
            metadata: Additional metadata

        Returns:
            str: Encoded JWT token
        """
        # Choose default expiration time based on role
        if custom_expire_hours is None:
            if role == "guest":
                expire_hours = self.guest_expire_hours
            else:
                expire_hours = self.expire_hours
        else:
            expire_hours = custom_expire_hours

        expire = datetime.utcnow() + timedelta(hours=expire_hours)

        # Create payload
        payload = TokenPayload(
            sub=username, exp=expire, role=role, metadata=metadata or {}
        )

        return jwt.encode(payload.dict(), self.secret, algorithm=self.algorithm)

    def validate_token(self, token: str) -> dict:
        """
        Validate JWT token

        Args:
            token: JWT token

        Returns:
            dict: Dictionary containing user information

        Raises:
            HTTPException: If token is invalid or expired
        """
        try:
            payload = jwt.decode(token, self.secret, algorithms=[self.algorithm])
            expire_timestamp = payload["exp"]
            expire_time = datetime.utcfromtimestamp(expire_timestamp)

            if datetime.utcnow() > expire_time:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
                )

            # Return complete payload instead of just username
            return {
                "username": payload["sub"],
                "role": payload.get("role", "user"),
                "metadata": payload.get("metadata", {}),
                "exp": expire_time,
            }
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            )


auth_handler = AuthHandler()
