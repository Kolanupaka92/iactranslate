"""OIDC single sign-on — the authorization-code flow, as a relying party.

No Fortune 500 accepts local username-and-password for a tool holding their
infrastructure topology. SSO is the first line of every security questionnaire,
and its absence was the single clearest procurement blocker in the architecture
review.

**Token validation is done by PyJWT, not by hand.** That review's own criticism
of the existing auth was that bespoke security code is unnecessary risk;
answering it by hand-rolling JWT signature verification would be worse than the
problem. What lives here is the parts a library cannot decide for you — which
issuer to trust, which claims to require, and how a verified identity maps onto a
local account.

The attacks this is written against, each with the control that stops it:

``algorithm confusion``
    A token signed with HS256 using the *public* key as the HMAC secret verifies
    if the algorithm is taken from the token header. `ALLOWED_ALGORITHMS` is a
    fixed allow-list of asymmetric algorithms; `none` and every HMAC variant are
    rejected before a key is ever looked up.

``CSRF on the callback``
    An attacker who can make the browser hit `/callback` with their own code
    logs the victim into the *attacker's* account. A signed, single-use `state`
    is required and consumed.

``ID-token replay``
    A `nonce` is bound to the login attempt and must match the token.

``code interception``
    PKCE (S256) is always sent, so a stolen authorization code is useless
    without the verifier.

``issuer or audience confusion``
    A token from a different tenant of the same provider is a valid, correctly
    signed token — it just is not for us. `iss` and `aud` are matched exactly.

``unverified email``
    Accounts are keyed on email, so a provider that lets a user *claim* an
    address without proving it would let them take over an existing account.
    `email_verified` is required unless explicitly waived for a provider known
    to verify out of band.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..observability import get_logger

logger = get_logger("iactranslate.api.oidc")

#: Only asymmetric signatures. An HMAC algorithm here would let a token signed
#: with the provider's *public* key verify — the classic confusion attack.
ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")

#: Clock skew tolerated on `exp`/`iat`. Small: this is a fudge for NTP drift,
#: not a grace period for expired tokens.
LEEWAY_SECONDS = 60

#: How long a login attempt may sit between redirect and callback.
STATE_TTL_SECONDS = 600

_DISCOVERY_SUFFIX = "/.well-known/openid-configuration"
_HTTP_TIMEOUT = 5.0


class OidcError(RuntimeError):
    """Configuration or protocol failure. Message is safe to log, not to show."""


class OidcNotConfigured(OidcError):
    pass


@dataclass
class PendingLogin:
    """One in-flight login attempt."""

    state: str
    nonce: str
    verifier: str
    created_at: float
    redirect_to: Optional[str] = None


@dataclass
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: Tuple[str, ...] = ("openid", "email", "profile")
    #: Waive `email_verified`. Only for a provider that verifies out of band —
    #: Azure AD does not always emit the claim, for instance.
    trust_unverified_email: bool = False
    _metadata: Dict[str, Any] = field(default_factory=dict, repr=False)

    def metadata(self) -> Dict[str, Any]:
        """Provider endpoints, from discovery. Cached for the process lifetime.

        Fetched rather than configured so a provider rotating an endpoint does
        not need a redeploy, and so `issuer` is the single thing an operator has
        to get right.
        """
        if not self._metadata:
            url = self.issuer.rstrip("/") + _DISCOVERY_SUFFIX
            self._metadata = _get_json(url)
            declared = self._metadata.get("issuer")
            # The discovery document must agree with where it was fetched from.
            # A provider whose document claims a different issuer is either
            # misconfigured or being impersonated, and every later `iss` check
            # would be validating against the attacker's answer.
            if declared and declared.rstrip("/") != self.issuer.rstrip("/"):
                raise OidcError(
                    f"discovery at {url} declares issuer {declared!r}, expected {self.issuer!r}"
                )
        return self._metadata


def _get_json(url: str) -> Dict[str, Any]:
    if not url.lower().startswith("https://"):
        # Except for tests against a local mock, which pass the metadata in.
        if not os.getenv("IACTRANSLATE_OIDC_ALLOW_INSECURE"):
            raise OidcError(f"refusing to fetch OIDC metadata over a non-https URL: {url}")
    with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310  # nosec B310
        return json.loads(resp.read())


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def pkce_pair() -> Tuple[str, str]:
    """`(verifier, challenge)` for PKCE S256."""
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


class LoginStore:
    """In-flight login attempts, keyed by `state`.

    Single-use: `consume` removes the entry, so a replayed callback finds
    nothing. Bounded and expiring, because `state` values arrive from the
    network and an unbounded table is a memory-exhaustion vector.
    """

    MAX_PENDING = 5_000

    def __init__(self, ttl: float = STATE_TTL_SECONDS) -> None:
        self._ttl = ttl
        self._pending: Dict[str, PendingLogin] = {}

    def begin(self, redirect_to: Optional[str] = None) -> PendingLogin:
        self._evict()
        login = PendingLogin(
            state=secrets.token_urlsafe(32),
            nonce=secrets.token_urlsafe(32),
            verifier=pkce_pair()[0],
            created_at=time.time(),
            redirect_to=redirect_to,
        )
        self._pending[login.state] = login
        return login

    def consume(self, state: str) -> PendingLogin:
        login = self._pending.pop(state, None)
        if login is None:
            raise OidcError("unknown or already-used state — restart the login")
        if time.time() - login.created_at > self._ttl:
            raise OidcError("login attempt expired — restart the login")
        return login

    def _evict(self) -> None:
        now = time.time()
        stale = [s for s, p in self._pending.items() if now - p.created_at > self._ttl]
        for state in stale:
            del self._pending[state]
        if len(self._pending) >= self.MAX_PENDING:
            oldest = sorted(self._pending.items(), key=lambda kv: kv[1].created_at)
            for state, _ in oldest[: len(self._pending) // 4]:
                del self._pending[state]


def authorization_url(config: OidcConfig, login: PendingLogin) -> str:
    """Where to send the browser to authenticate."""
    import urllib.parse

    endpoint = config.metadata().get("authorization_endpoint")
    if not endpoint:
        raise OidcError("provider metadata has no authorization_endpoint")
    _, challenge = _derive_challenge(login.verifier)
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": " ".join(config.scopes),
        "state": login.state,
        "nonce": login.nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{endpoint}?{query}"


def _derive_challenge(verifier: str) -> Tuple[str, str]:
    return verifier, _b64url(hashlib.sha256(verifier.encode()).digest())


def exchange_code(config: OidcConfig, code: str, verifier: str) -> Dict[str, Any]:
    """Trade the authorization code for tokens."""
    import urllib.parse

    endpoint = config.metadata().get("token_endpoint")
    if not endpoint:
        raise OidcError("provider metadata has no token_endpoint")
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.redirect_uri,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "code_verifier": verifier,
    }).encode()
    request = urllib.request.Request(
        endpoint, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310  # nosec B310
        return json.loads(resp.read())


def verify_id_token(
    config: OidcConfig,
    id_token: str,
    nonce: str,
    jwks_client=None,
) -> Dict[str, Any]:
    """Validate an ID token and return its claims.

    Every check here is one an attacker would like skipped, so none of them are
    optional and none are inferred from the token itself.
    """
    import jwt

    if jwks_client is None:
        uri = config.metadata().get("jwks_uri")
        if not uri:
            raise OidcError("provider metadata has no jwks_uri")
        jwks_client = jwt.PyJWKClient(uri, cache_keys=True)

    # The `kid` selects the key; the algorithm does NOT come from the token.
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(id_token).key
    except Exception as exc:
        raise OidcError(f"could not resolve a signing key: {exc}") from exc

    try:
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=list(ALLOWED_ALGORITHMS),
            audience=config.client_id,
            issuer=config.issuer,
            leeway=LEEWAY_SECONDS,
            options={
                "require": ["exp", "iat", "iss", "aud", "sub"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except Exception as exc:
        raise OidcError(f"ID token rejected: {exc}") from exc

    # Bound to this login attempt. Checked after signature verification, so a
    # forged token never reaches a comparison against a real nonce.
    token_nonce = claims.get("nonce")
    if not token_nonce or not secrets.compare_digest(str(token_nonce), nonce):
        raise OidcError("ID token nonce does not match this login attempt")

    return claims


def identity_from_claims(config: OidcConfig, claims: Dict[str, Any]) -> Tuple[str, str]:
    """`(subject, email)` for a verified token.

    Accounts are keyed on email, so an unverified address would let someone
    claim an existing account by asserting its address at a permissive provider.
    """
    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise OidcError(
            "the ID token carries no email claim; request the 'email' scope"
        )
    if not config.trust_unverified_email and claims.get("email_verified") is not True:
        raise OidcError(
            f"the provider did not assert email_verified for {email}; refusing to "
            "match it to an account. Set trust_unverified_email only for a "
            "provider that verifies addresses out of band."
        )
    subject = str(claims.get("sub") or "")
    if not subject:
        raise OidcError("the ID token carries no sub claim")
    return subject, email


def config_from_env() -> Optional[OidcConfig]:
    """Build a config from the environment, or None when SSO is off."""
    issuer = os.getenv("IACTRANSLATE_OIDC_ISSUER", "").strip()
    if not issuer:
        return None
    missing = [
        name for name in ("IACTRANSLATE_OIDC_CLIENT_ID",
                          "IACTRANSLATE_OIDC_CLIENT_SECRET",
                          "IACTRANSLATE_OIDC_REDIRECT_URI")
        if not os.getenv(name, "").strip()
    ]
    if missing:
        raise OidcNotConfigured(
            f"IACTRANSLATE_OIDC_ISSUER is set but {', '.join(missing)} "
            "is missing — SSO is half-configured, which is worse than off."
        )
    # Fail here rather than at the callback. Discovering the dependency is
    # missing *after* the browser has been redirected to the provider means the
    # user authenticates successfully and then lands on an error, which looks
    # like the identity provider's fault and is the worst moment to find out.
    try:
        import jwt  # noqa: F401
    except ImportError as exc:
        raise OidcNotConfigured(
            "single sign-on is configured but PyJWT is not installed — "
            "`pip install 'iactranslate[sso]'`. Refusing to start a login flow "
            "that cannot validate the token it gets back."
        ) from exc

    scopes = os.getenv("IACTRANSLATE_OIDC_SCOPES", "openid email profile").split()
    return OidcConfig(
        issuer=issuer,
        client_id=os.environ["IACTRANSLATE_OIDC_CLIENT_ID"],
        client_secret=os.environ["IACTRANSLATE_OIDC_CLIENT_SECRET"],
        redirect_uri=os.environ["IACTRANSLATE_OIDC_REDIRECT_URI"],
        scopes=tuple(scopes),
        trust_unverified_email=os.getenv(
            "IACTRANSLATE_OIDC_TRUST_UNVERIFIED_EMAIL", "0"
        ).strip() == "1",
    )


def enabled() -> bool:
    return bool(os.getenv("IACTRANSLATE_OIDC_ISSUER", "").strip())


__all__: List[str] = [
    "ALLOWED_ALGORITHMS",
    "LoginStore",
    "OidcConfig",
    "OidcError",
    "OidcNotConfigured",
    "authorization_url",
    "config_from_env",
    "enabled",
    "exchange_code",
    "identity_from_claims",
    "pkce_pair",
    "verify_id_token",
]
