"""SAML single sign-on, tested against a mock identity provider we control.

As with OIDC (test_sso.py), the happy path is a few lines and most of this file
is attacks: unsigned and forged assertions, signature wrapping, replay, login
CSRF, XXE, and an IdP asserting an address outside its domains. Each must end
with the browser back at sign-in and no session.

Not exercised: a real IdP's quirks. That is the first live check.
"""
from __future__ import annotations

import base64
import datetime as dt
import importlib
import time
import urllib.parse
import uuid
import zlib

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from lxml import etree

pytest.importorskip("signxml")
from signxml import XMLSigner  # noqa: E402

from iactranslate.api import saml  # noqa: E402

APP = "https://app.example"
IDP = "https://idp.corp.test/entity"
SSO = "https://idp.corp.test/sso"
SP = f"{APP}/api/v1/auth/saml/metadata"
ACS = f"{APP}/api/v1/auth/saml/acs"
EXC_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"


def _keypair(cn="idp.corp.test"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=365)).sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return key_pem, cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture(scope="module")
def idp():
    return _keypair()


@pytest.fixture(scope="module")
def attacker():
    return _keypair("attacker.test")


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _assertion_xml(*, request_id, email="pat@corp.test", issuer=IDP, audience=SP, recipient=ACS,
                   not_before=None, not_on_or_after=None, name_id_format=None, attributes=None) -> str:
    now = time.time()
    nb = _iso(not_before if not_before is not None else now - 60)
    noa = _iso(not_on_or_after if not_on_or_after is not None else now + 300)
    fmt = name_id_format or "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
    attrs = "".join(
        f'<saml:Attribute Name="{n}"><saml:AttributeValue>{v}</saml:AttributeValue></saml:Attribute>'
        for n, v in (attributes or {}).items())
    in_response = f' InResponseTo="{request_id}"' if request_id else ""
    return (
        f'<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_a{uuid.uuid4().hex}" '
        f'Version="2.0" IssueInstant="{_iso(now)}">'
        f"<saml:Issuer>{issuer}</saml:Issuer>"
        f'<saml:Subject><saml:NameID Format="{fmt}">{email}</saml:NameID>'
        '<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
        f'<saml:SubjectConfirmationData{in_response} Recipient="{recipient}" NotOnOrAfter="{noa}"/>'
        "</saml:SubjectConfirmation></saml:Subject>"
        f'<saml:Conditions NotBefore="{nb}" NotOnOrAfter="{noa}">'
        f"<saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction>"
        "</saml:Conditions>"
        + (f"<saml:AttributeStatement>{attrs}</saml:AttributeStatement>" if attrs else "")
        + "</saml:Assertion>"
    )


def _sign(element, keypair, algorithm="rsa-sha256", digest="sha256"):
    key, cert = keypair
    signer = XMLSigner(signature_algorithm=algorithm, digest_algorithm=digest, c14n_algorithm=EXC_C14N)
    return signer.sign(element, key=key, cert=cert)


def _response(*, assertion_xml, keypair=None, sign="assertion", status="Success", extra="",
              doctype="", algorithm="rsa-sha256", digest="sha256") -> str:
    assertion = etree.fromstring(assertion_xml.encode())
    if sign == "assertion" and keypair:
        assertion = _sign(assertion, keypair, algorithm, digest)
    response = etree.fromstring(
        f'<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        f'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_r{uuid.uuid4().hex}" Version="2.0" '
        f'IssueInstant="{_iso(time.time())}" Destination="{ACS}">'
        f"<saml:Issuer>{IDP}</saml:Issuer>"
        f'<samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:{status}"/></samlp:Status>'
        "</samlp:Response>".encode())
    response.append(assertion)
    if extra:
        response.append(etree.fromstring(extra.encode()))
    if sign == "response" and keypair:
        response = _sign(response, keypair, algorithm, digest)
    body = etree.tostring(response).decode()
    return base64.b64encode((doctype + body).encode()).decode()


@pytest.fixture()
def api(tmp_path, monkeypatch, idp):
    monkeypatch.setenv("IACTRANSLATE_AUTH", "session")
    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "saml.db"))
    monkeypatch.setenv("IACTRANSLATE_RATE_AUTH", "0")
    monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)
    monkeypatch.setenv("IACTRANSLATE_APP_URL", APP)
    monkeypatch.setenv("IACTRANSLATE_SAML_IDP_ENTITY_ID", IDP)
    monkeypatch.setenv("IACTRANSLATE_SAML_IDP_SSO_URL", SSO)
    monkeypatch.setenv("IACTRANSLATE_SAML_IDP_CERT", idp[1])
    monkeypatch.setenv("IACTRANSLATE_SAML_EMAIL_DOMAINS", "corp.test, @Sub.Corp.Test")
    import iactranslate.api.main as main
    importlib.reload(main)
    yield main
    monkeypatch.undo()
    importlib.reload(main)


def _browser(api) -> TestClient:
    # https base URL: the request cookie is Secure.
    return TestClient(api.app, base_url="https://testserver")


def _start(client) -> str:
    r = client.get("/auth/saml/login", follow_redirects=False)
    assert r.status_code == 302
    return client.cookies.get(saml.REQUEST_COOKIE)


def _post(client, saml_response):
    return client.post("/auth/saml/acs", data={"SAMLResponse": saml_response}, follow_redirects=False)


def _signed_in(client) -> bool:
    return client.get("/auth/me").status_code == 200


def _refused(r, client) -> bool:
    return (r.status_code == 303 and r.headers["location"] == f"{APP}/console?sso=failed"
            and not _signed_in(client))


# --- the flow ------------------------------------------------------------------------------


def test_login_redirects_with_a_bound_request(api):
    client = _browser(api)
    r = client.get("/auth/saml/login", follow_redirects=False)
    location = urllib.parse.urlsplit(r.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == SSO
    raw = base64.b64decode(urllib.parse.parse_qs(location.query)["SAMLRequest"][0])
    request = etree.fromstring(zlib.decompress(raw, -15))
    assert request.get("ID") == client.cookies.get(saml.REQUEST_COOKIE)
    assert request.get("Destination") == SSO and request.get("AssertionConsumerServiceURL") == ACS
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=none" in cookie


def test_a_signed_assertion_signs_you_in(api, idp):
    client = _browser(api)
    rid = _start(client)
    r = _post(client, _response(assertion_xml=_assertion_xml(request_id=rid), keypair=idp))
    assert r.status_code == 303 and r.headers["location"] == f"{APP}/console"
    me = client.get("/auth/me").json()
    assert me["email"] == "pat@corp.test" and me["email_verified"] is True


def test_a_signed_response_works_too(api, idp):
    client = _browser(api)
    rid = _start(client)
    r = _post(client, _response(assertion_xml=_assertion_xml(request_id=rid), keypair=idp, sign="response"))
    assert r.headers["location"] == f"{APP}/console" and _signed_in(client)


def test_email_from_an_attribute_when_the_name_id_is_opaque(api, idp):
    client = _browser(api)
    rid = _start(client)
    xml = _assertion_xml(request_id=rid, email="0f8e2a", attributes={"mail": "sam@sub.corp.test"},
                         name_id_format="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent")
    assert _post(client, _response(assertion_xml=xml, keypair=idp)).headers["location"] == f"{APP}/console"
    assert client.get("/auth/me").json()["email"] == "sam@sub.corp.test"


def test_either_certificate_works_during_a_rotation(api, idp, monkeypatch):
    new = _keypair("idp-2026.corp.test")
    monkeypatch.setenv("IACTRANSLATE_SAML_IDP_CERT", idp[1] + "\n" + new[1])
    importlib.reload(api)
    client = _browser(api)
    rid = _start(client)
    assert _post(client, _response(assertion_xml=_assertion_xml(request_id=rid), keypair=new)).headers[
        "location"] == f"{APP}/console"


# --- forged and altered --------------------------------------------------------------------


def test_an_unsigned_assertion_is_refused(api):
    client = _browser(api)
    rid = _start(client)
    assert _refused(_post(client, _response(assertion_xml=_assertion_xml(request_id=rid))), client)


def test_a_signature_from_another_key_is_refused(api, attacker):
    """The certificate is pinned: one carried in the message counts for nothing."""
    client = _browser(api)
    rid = _start(client)
    r = _post(client, _response(assertion_xml=_assertion_xml(request_id=rid), keypair=attacker))
    assert _refused(r, client)


def test_an_assertion_altered_after_signing_is_refused(api, idp):
    client = _browser(api)
    rid = _start(client)
    signed = base64.b64decode(_response(assertion_xml=_assertion_xml(request_id=rid), keypair=idp)).decode()
    tampered = signed.replace("pat@corp.test", "ceo@corp.test")
    assert _refused(_post(client, base64.b64encode(tampered.encode()).decode()), client)


def test_signature_wrapping_with_a_second_assertion_is_refused(api, idp):
    """A validly signed assertion for pat@, plus an unsigned one for ceo@ that a
    careless parser would read first."""
    client = _browser(api)
    rid = _start(client)
    evil = _assertion_xml(request_id=rid, email="ceo@corp.test")
    r = _post(client, _response(assertion_xml=_assertion_xml(request_id=rid), keypair=idp, extra=evil))
    assert _refused(r, client)


def test_only_strong_signature_algorithms_are_accepted(api, idp):
    """RSA-SHA224 is a valid signature that signxml accepts by default; our
    allowlist (RSA/ECDSA with SHA-256 or stronger) refuses it. SHA-1 is refused
    by signxml itself, which won't even create one to test with."""
    client = _browser(api)
    rid = _start(client)
    r = _post(client, _response(assertion_xml=_assertion_xml(request_id=rid), keypair=idp,
                                algorithm="rsa-sha224", digest="sha256"))
    assert _refused(r, client)


def test_a_doctype_is_refused(api, idp):
    client = _browser(api)
    rid = _start(client)
    xxe = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    r = _post(client, _response(assertion_xml=_assertion_xml(request_id=rid), keypair=idp, doctype=xxe))
    assert _refused(r, client)


def test_encrypted_assertions_are_refused(api, idp):
    client = _browser(api)
    rid = _start(client)
    enc = '<saml:EncryptedAssertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"/>'
    r = _post(client, _response(assertion_xml=_assertion_xml(request_id=rid), keypair=idp, extra=enc))
    assert _refused(r, client)


# --- right signature, wrong assertion -------------------------------------------------------


@pytest.mark.parametrize("overrides", [
    {"issuer": "https://other-idp.test"},
    {"audience": "https://another-sp.test"},
    {"recipient": "https://evil.test/acs"},
    {"not_on_or_after": time.time() - 600},
    {"not_before": time.time() + 600},
    {"email": "pat@gmail.com"},           # outside the IdP's domains
    {"email": "pat@corp.test.evil.com"},  # suffix trick
    {"email": "not-an-email"},
], ids=["issuer", "audience", "recipient", "expired", "not-yet-valid", "foreign-domain",
        "domain-suffix", "no-email"])
def test_a_signed_but_wrong_assertion_is_refused(api, idp, overrides):
    client = _browser(api)
    rid = _start(client)
    r = _post(client, _response(assertion_xml=_assertion_xml(request_id=rid, **overrides), keypair=idp))
    assert _refused(r, client)


def test_an_idp_error_status_is_refused(api, idp):
    client = _browser(api)
    rid = _start(client)
    r = _post(client, _response(assertion_xml=_assertion_xml(request_id=rid), keypair=idp, status="Requester"))
    assert _refused(r, client)


# --- bound to this browser, this request, once ------------------------------------------------


def test_a_response_to_another_request_is_refused(api, idp):
    client = _browser(api)
    _start(client)
    r = _post(client, _response(assertion_xml=_assertion_xml(request_id="_someone-elses"), keypair=idp))
    assert _refused(r, client)


def test_an_unsolicited_response_is_refused(api, idp):
    """IdP-initiated sign-in carries no InResponseTo; it is refused."""
    client = _browser(api)
    _start(client)
    r = _post(client, _response(assertion_xml=_assertion_xml(request_id=None), keypair=idp))
    assert _refused(r, client)


def test_login_csrf_is_refused(api, idp):
    """An attacker starts a sign-in, gets a valid assertion for their own
    account, and makes the victim's browser post it. The victim's browser has
    no matching request cookie, so it signs nobody in."""
    attacker_browser = _browser(api)
    rid = _start(attacker_browser)
    attackers_assertion = _response(assertion_xml=_assertion_xml(request_id=rid, email="mallory@corp.test"),
                                    keypair=idp)
    victim = _browser(api)
    assert _refused(_post(victim, attackers_assertion), victim)


def test_a_response_works_once(api, idp):
    client = _browser(api)
    rid = _start(client)
    response = _response(assertion_xml=_assertion_xml(request_id=rid), keypair=idp)
    assert _post(client, response).headers["location"] == f"{APP}/console"
    replay = _browser(api)
    replay.cookies.set(saml.REQUEST_COOKIE, rid, domain="testserver")
    assert _refused(_post(replay, response), replay)


# --- accounts -----------------------------------------------------------------------------


def test_saml_reclaims_an_unverified_account(api, idp):
    squatter = _browser(api)
    squatter.post("/auth/register", json={"email": "pat@corp.test", "password": "correct-horse-battery"})
    assert _signed_in(squatter)
    client = _browser(api)
    rid = _start(client)
    _post(client, _response(assertion_xml=_assertion_xml(request_id=rid), keypair=idp))
    assert not _signed_in(squatter)
    r = _browser(api).post("/auth/login", json={"email": "pat@corp.test", "password": "correct-horse-battery"})
    assert r.status_code == 401


# --- configuration and discovery --------------------------------------------------------------


def test_metadata_and_methods(api):
    client = _browser(api)
    md = etree.fromstring(client.get("/auth/saml/metadata").content)
    assert md.get("entityID") == SP
    acs = md.find(".//{urn:oasis:names:tc:SAML:2.0:metadata}AssertionConsumerService")
    assert acs.get("Location") == ACS
    assert client.get("/auth/methods").json() == {"password": True, "saml": True, "oidc": False}


def test_saml_is_off_without_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_AUTH", "session")
    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "off.db"))
    monkeypatch.delenv("IACTRANSLATE_SAML_IDP_ENTITY_ID", raising=False)
    import iactranslate.api.main as main
    importlib.reload(main)
    try:
        client = TestClient(main.app, base_url="https://testserver")
        assert client.get("/auth/saml/login", follow_redirects=False).status_code == 404
        assert client.get("/auth/methods").json()["saml"] is False
    finally:
        monkeypatch.undo()
        importlib.reload(main)


@pytest.mark.parametrize("env,match", [
    ({"IACTRANSLATE_SAML_IDP_ENTITY_ID": IDP}, "needs"),
    ({"IACTRANSLATE_SAML_IDP_ENTITY_ID": IDP, "IACTRANSLATE_SAML_IDP_SSO_URL": SSO,
      "IACTRANSLATE_SAML_IDP_CERT": "nope", "IACTRANSLATE_SAML_EMAIL_DOMAINS": "corp.test"}, "no PEM"),
    ({"IACTRANSLATE_SAML_IDP_ENTITY_ID": IDP, "IACTRANSLATE_SAML_IDP_SSO_URL": "http://idp.test/sso",
      "IACTRANSLATE_SAML_IDP_CERT": "x", "IACTRANSLATE_SAML_EMAIL_DOMAINS": "corp.test"}, "https"),
])
def test_half_configured_saml_refuses_to_start(env, match):
    with pytest.raises(saml.SamlConfigError, match=match):
        saml.config_from_env(env)


@pytest.mark.parametrize("value,expected", [
    ("2026-11-23T10:00:00Z", 1795428000.0),
    ("2026-11-23T10:00:00.5Z", 1795428000.5),
    ("2026-11-23T10:00:00.123456789Z", 1795428000.123456789),
    ("2026-11-23T12:00:00+02:00", 1795428000.0),
])
def test_saml_times_parse(value, expected):
    assert saml._parse_time(value, "t") == pytest.approx(expected)
