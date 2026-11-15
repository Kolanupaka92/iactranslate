"""OIDC single sign-on, tested against a mock issuer we control.

An identity provider we own lets us mint tokens an attacker would mint. Almost
every test here is an attack: the happy path is one line, and the value is in
what gets refused.

A real-provider integration is still outstanding — this proves the relying-party
logic, not that any particular vendor's quirks are handled.
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from iactranslate.api.oidc import (
    ALLOWED_ALGORITHMS,
    LEEWAY_SECONDS,
    LoginStore,
    OidcConfig,
    OidcError,
    OidcNotConfigured,
    authorization_url,
    config_from_env,
    enabled,
    identity_from_claims,
    pkce_pair,
    verify_id_token,
)

ISSUER = "https://idp.test"
CLIENT_ID = "iactranslate-client"


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def attacker_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def config():
    return OidcConfig(
        issuer=ISSUER, client_id=CLIENT_ID, client_secret="shh",
        redirect_uri="https://app.test/v1/auth/sso/callback",
        _metadata={
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "jwks_uri": f"{ISSUER}/jwks",
        },
    )


@pytest.fixture
def jwks(signing_key):
    """A JWKS client that always returns the issuer's real public key."""
    class _Jwks:
        def get_signing_key_from_jwt(self, token):
            class _Key:
                key = signing_key.public_key()
            return _Key()
    return _Jwks()


def mint(key, alg="RS256", **overrides):
    now = int(time.time())
    claims = {
        "iss": ISSUER, "aud": CLIENT_ID, "sub": "subject-1",
        "exp": now + 300, "iat": now, "nonce": "the-nonce",
        "email": "person@acme.test", "email_verified": True,
    }
    claims.update(overrides)
    for empty in [k for k, v in claims.items() if v is None]:
        del claims[empty]
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": "k1"})


# --- the happy path ----------------------------------------------------------

def test_a_valid_token_is_accepted(config, signing_key, jwks):
    claims = verify_id_token(config, mint(signing_key), "the-nonce", jwks)
    assert claims["sub"] == "subject-1"
    assert identity_from_claims(config, claims) == ("subject-1", "person@acme.test")


# --- signature and algorithm -------------------------------------------------

def test_no_hmac_algorithm_is_accepted():
    """Algorithm confusion: a token signed with HS256 using the provider's
    *public* key as the HMAC secret verifies, if the algorithm is taken from the
    token header. The allow-list is the control."""
    assert not any(a.startswith("HS") for a in ALLOWED_ALGORITHMS)
    assert "none" not in [a.lower() for a in ALLOWED_ALGORITHMS]


def test_a_token_signed_by_someone_else_is_rejected(config, attacker_key, jwks):
    with pytest.raises(OidcError):
        verify_id_token(config, mint(attacker_key), "the-nonce", jwks)


def test_an_unsigned_token_is_rejected(config, jwks):
    unsigned = jwt.encode({"iss": ISSUER, "aud": CLIENT_ID, "sub": "x"}, None, algorithm="none")
    with pytest.raises(OidcError):
        verify_id_token(config, unsigned, "the-nonce", jwks)


def test_a_tampered_payload_is_rejected(config, signing_key, jwks):
    header, payload, sig = mint(signing_key).split(".")
    other = mint(signing_key, sub="somebody-else").split(".")[1]
    with pytest.raises(OidcError):
        verify_id_token(config, f"{header}.{other}.{sig}", "the-nonce", jwks)


# --- claim validation --------------------------------------------------------

def test_a_token_from_another_issuer_is_rejected(config, signing_key, jwks):
    with pytest.raises(OidcError):
        verify_id_token(config, mint(signing_key, iss="https://evil.test"), "the-nonce", jwks)


def test_a_token_for_another_client_is_rejected(config, signing_key, jwks):
    """A token for a different tenant of the same provider is valid and correctly
    signed — it is simply not for us."""
    with pytest.raises(OidcError):
        verify_id_token(config, mint(signing_key, aud="someone-elses-app"), "the-nonce", jwks)


def test_an_expired_token_is_rejected(config, signing_key, jwks):
    now = int(time.time())
    with pytest.raises(OidcError):
        verify_id_token(config, mint(signing_key, exp=now - 3600, iat=now - 7200),
                        "the-nonce", jwks)


def test_a_token_just_inside_the_skew_allowance_is_accepted(config, signing_key, jwks):
    """The leeway is a fudge for NTP drift, not a grace period."""
    now = int(time.time())
    claims = verify_id_token(
        config, mint(signing_key, exp=now - (LEEWAY_SECONDS // 2)), "the-nonce", jwks
    )
    assert claims["sub"] == "subject-1"


@pytest.mark.parametrize("missing", ["exp", "iat", "sub"])
def test_a_token_missing_a_required_claim_is_rejected(config, signing_key, jwks, missing):
    with pytest.raises(OidcError):
        verify_id_token(config, mint(signing_key, **{missing: None}), "the-nonce", jwks)


def test_a_replayed_token_from_another_login_is_rejected(config, signing_key, jwks):
    """The nonce binds the token to one login attempt."""
    with pytest.raises(OidcError) as e:
        verify_id_token(config, mint(signing_key), "a-different-nonce", jwks)
    assert "nonce" in str(e.value)


def test_a_token_with_no_nonce_is_rejected(config, signing_key, jwks):
    with pytest.raises(OidcError):
        verify_id_token(config, mint(signing_key, nonce=None), "the-nonce", jwks)


# --- identity mapping --------------------------------------------------------

def test_an_unverified_email_is_refused(config, signing_key, jwks):
    """Accounts are keyed on email, so a provider that lets a user *claim* an
    address without proving it would allow account takeover."""
    claims = verify_id_token(config, mint(signing_key, email_verified=False),
                             "the-nonce", jwks)
    with pytest.raises(OidcError) as e:
        identity_from_claims(config, claims)
    assert "email_verified" in str(e.value)


def test_unverified_email_can_be_waived_deliberately(config, signing_key, jwks):
    config.trust_unverified_email = True
    claims = verify_id_token(config, mint(signing_key, email_verified=False),
                             "the-nonce", jwks)
    assert identity_from_claims(config, claims)[1] == "person@acme.test"


def test_a_token_with_no_email_is_refused(config, signing_key, jwks):
    claims = verify_id_token(config, mint(signing_key, email=None), "the-nonce", jwks)
    with pytest.raises(OidcError) as e:
        identity_from_claims(config, claims)
    assert "email" in str(e.value)


def test_emails_are_normalised(config, signing_key, jwks):
    """Otherwise `Person@ACME.test` and `person@acme.test` are two accounts."""
    claims = verify_id_token(config, mint(signing_key, email="  Person@ACME.test "),
                             "the-nonce", jwks)
    assert identity_from_claims(config, claims)[1] == "person@acme.test"


# --- the login handshake -----------------------------------------------------

def test_state_is_single_use():
    """A replayed callback must find nothing."""
    store = LoginStore()
    login = store.begin()
    assert store.consume(login.state).state == login.state
    with pytest.raises(OidcError):
        store.consume(login.state)


def test_an_unknown_state_is_rejected():
    """CSRF: an attacker making the browser hit /callback with their own code
    would otherwise log the victim into the attacker's account."""
    with pytest.raises(OidcError):
        LoginStore().consume("state-we-never-issued")


def test_an_expired_login_attempt_is_rejected():
    store = LoginStore(ttl=-1)
    login = store.begin()
    with pytest.raises(OidcError):
        store.consume(login.state)


def test_state_and_nonce_are_unpredictable():
    store = LoginStore()
    states = {store.begin().state for _ in range(200)}
    assert len(states) == 200
    assert all(len(s) >= 32 for s in states)


def test_pkce_challenge_is_the_sha256_of_the_verifier():
    import base64
    import hashlib

    verifier, challenge = pkce_pair()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    assert challenge == expected


def test_the_authorization_url_carries_every_control(config):
    login = LoginStore().begin()
    url = authorization_url(config, login)
    for required in ("response_type=code", "code_challenge_method=S256",
                     "code_challenge=", f"state={login.state}", f"nonce={login.nonce}"):
        assert required in url, f"missing {required}"


# --- configuration -----------------------------------------------------------

def test_sso_is_off_without_an_issuer(monkeypatch):
    monkeypatch.delenv("IACTRANSLATE_OIDC_ISSUER", raising=False)
    assert config_from_env() is None
    assert not enabled()


def test_half_configured_sso_fails_loudly(monkeypatch):
    """Worse than off: an operator who set the issuer believes SSO is on."""
    monkeypatch.setenv("IACTRANSLATE_OIDC_ISSUER", ISSUER)
    monkeypatch.delenv("IACTRANSLATE_OIDC_CLIENT_ID", raising=False)
    monkeypatch.delenv("IACTRANSLATE_OIDC_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("IACTRANSLATE_OIDC_REDIRECT_URI", raising=False)
    with pytest.raises(OidcNotConfigured) as e:
        config_from_env()
    assert "CLIENT_ID" in str(e.value)


def test_discovery_must_agree_with_where_it_was_fetched(monkeypatch):
    """A document claiming a different issuer is misconfigured or impersonated,
    and every later `iss` check would validate against the attacker's answer."""
    from iactranslate.api import oidc

    monkeypatch.setattr(oidc, "_get_json", lambda url: {"issuer": "https://evil.test"})
    config = OidcConfig(issuer=ISSUER, client_id="c", client_secret="s", redirect_uri="r")
    with pytest.raises(OidcError) as e:
        config.metadata()
    assert "declares issuer" in str(e.value)


def test_metadata_is_not_fetched_over_plaintext(monkeypatch):
    monkeypatch.delenv("IACTRANSLATE_OIDC_ALLOW_INSECURE", raising=False)

    config = OidcConfig(issuer="http://idp.test", client_id="c",
                        client_secret="s", redirect_uri="r")
    with pytest.raises(OidcError) as e:
        config.metadata()
    assert "non-https" in str(e.value)


# --- the API surface ---------------------------------------------------------

def test_sso_endpoints_404_when_it_is_not_configured():
    from fastapi.testclient import TestClient

    from iactranslate.api.main import app
    client = TestClient(app)
    assert client.get("/v1/auth/sso/login", follow_redirects=False).status_code == 404
    assert client.get("/v1/auth/sso/callback?code=x&state=y").status_code == 404


def test_missing_pyjwt_fails_at_startup_not_at_the_callback(monkeypatch):
    """Discovering the dependency is absent *after* redirecting the browser
    means the user authenticates and then lands on an error that looks like the
    provider's fault."""
    import builtins

    monkeypatch.setenv("IACTRANSLATE_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("IACTRANSLATE_OIDC_CLIENT_ID", "c")
    monkeypatch.setenv("IACTRANSLATE_OIDC_CLIENT_SECRET", "s")
    monkeypatch.setenv("IACTRANSLATE_OIDC_REDIRECT_URI", "https://app.test/cb")

    real_import = builtins.__import__

    def no_jwt(name, *args, **kwargs):
        if name == "jwt":
            raise ImportError("no module named jwt")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_jwt)
    with pytest.raises(OidcNotConfigured) as e:
        config_from_env()
    assert "iactranslate[sso]" in str(e.value)
