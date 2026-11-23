"""SAML 2.0 single sign-on — the service-provider side (ADR 0081).

Many enterprises run identity providers that speak SAML and not OIDC (older
ADFS farms, some Okta and Ping set-ups by policy). This is the relying party for
them: SP-initiated sign-in, HTTP-Redirect for the request, HTTP-POST for the
response.

**Why signxml and not python3-saml.** python3-saml is the usual choice, but it
sits on the `xmlsec` C library, which has no wheel for Python 3.15 (the image's
base) and is known for libxml2 version clashes with lxml. signxml is pure Python
over lxml and cryptography. What python3-saml adds on top of signature checking
is the protocol validation below, and every check is listed and tested.

**XML signatures are where SAML implementations break.** The rules here exist
because each one closes a known class of bypass:

* **Only the signed copy is read.** signxml returns the element its signature
  covers; identity is read from that element and nothing else. Reading the
  original tree after verifying is the signature-wrapping bug.
* **Exactly one assertion, and no encrypted ones.** A second, unsigned assertion
  slipped next to a signed one is the other half of wrapping.
* **The certificate is pinned.** Signatures verify against the configured IdP
  certificate, never one carried in the message. Algorithms are limited to RSA
  or ECDSA with SHA-256 or stronger.
* **No DTDs.** A DOCTYPE is refused outright, which rules out entity expansion
  and external entities (XXE); the parser also resolves nothing.
* **Bound to this browser and this request.** The assertion's InResponseTo must
  be the request this browser started (kept in a cookie, and single-use in the
  database). IdP-initiated and replayed responses therefore fail, and so does
  logging a victim into an attacker's account with the attacker's assertion.
* **Issuer, audience, recipient and time** are all checked, with 2 minutes of
  clock skew.
* **Allowed domains.** The IdP is trusted only for the email domains the
  operator lists. Without that, a configured IdP could assert any address,
  someone's personal account included, and sign in as them.
"""
from __future__ import annotations

import base64
import os
import re
import secrets
import time
import urllib.parse
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from xml.sax.saxutils import escape, quoteattr

from .accounts import validate_email
from .sql import Database, backend, create_database

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "md": "urn:oasis:names:tc:SAML:2.0:metadata",
}
_SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
_BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
_EMAIL_FORMAT = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
#: Attribute names IdPs commonly use for the email address.
_EMAIL_ATTRIBUTES = (
    "email", "mail", "emailaddress", "urn:oid:0.9.2342.19200300.100.1.3",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
)

CLOCK_SKEW_SECONDS = 120
REQUEST_TTL_SECONDS = 600
MAX_RESPONSE_BYTES = 256 * 1024
REQUEST_COOKIE = "iactranslate_saml_request"

ENV_IDP_ENTITY_ID = "IACTRANSLATE_SAML_IDP_ENTITY_ID"
ENV_IDP_SSO_URL = "IACTRANSLATE_SAML_IDP_SSO_URL"
ENV_IDP_CERT = "IACTRANSLATE_SAML_IDP_CERT"
ENV_IDP_CERT_FILE = "IACTRANSLATE_SAML_IDP_CERT_FILE"
ENV_EMAIL_DOMAINS = "IACTRANSLATE_SAML_EMAIL_DOMAINS"
ENV_SP_ENTITY_ID = "IACTRANSLATE_SAML_SP_ENTITY_ID"
ENV_ACS_URL = "IACTRANSLATE_SAML_ACS_URL"

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class SamlError(Exception):
    """A response was refused. The message is for logs, not for the browser."""


class SamlConfigError(ValueError):
    """SAML is partly configured and cannot work as written."""


@dataclass
class SamlConfig:
    idp_entity_id: str
    idp_sso_url: str
    #: PEM certificates; more than one during an IdP key rotation.
    idp_certs: List[str]
    sp_entity_id: str
    acs_url: str
    email_domains: frozenset = field(default_factory=frozenset)


def _https(url: str, name: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS):
        return url
    raise SamlConfigError(f"{name} must be an https:// URL")


def _split_pem(text: str) -> List[str]:
    certs = re.findall(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", text, re.S)
    if not certs:
        raise SamlConfigError(f"{ENV_IDP_CERT} holds no PEM certificate")
    from cryptography import x509
    for pem in certs:
        try:
            x509.load_pem_x509_certificate(pem.encode())
        except ValueError as exc:
            raise SamlConfigError(f"{ENV_IDP_CERT} holds a certificate that doesn't parse") from exc
    return certs


def config_from_env(env=os.environ) -> Optional[SamlConfig]:
    """The SAML configuration, or None when SAML is off. A partial set raises:
    a deployment that believes it has SSO and silently doesn't is worse than
    one that refuses to start and names the missing variable."""
    def get(name: str) -> str:
        return (env.get(name) or "").strip()

    if not get(ENV_IDP_ENTITY_ID):
        return None
    missing = [n for n in (ENV_IDP_SSO_URL, ENV_EMAIL_DOMAINS) if not get(n)]
    if not get(ENV_IDP_CERT) and not get(ENV_IDP_CERT_FILE):
        missing.append(f"{ENV_IDP_CERT} or {ENV_IDP_CERT_FILE}")
    if missing:
        raise SamlConfigError(f"SAML needs {', '.join(missing)} as well as {ENV_IDP_ENTITY_ID}")
    try:
        import signxml  # noqa: F401
    except ImportError as exc:
        raise SamlConfigError("SAML needs signxml: pip install 'iactranslate[saml]'") from exc

    pem = get(ENV_IDP_CERT)
    if not pem:
        try:
            with open(get(ENV_IDP_CERT_FILE), encoding="utf-8") as fh:
                pem = fh.read()
        except OSError as exc:
            raise SamlConfigError(f"{ENV_IDP_CERT_FILE} can't be read") from exc
    app = (env.get("IACTRANSLATE_APP_URL") or "http://localhost:3000").rstrip("/")
    domains = frozenset(d.strip().lower().lstrip("@") for d in get(ENV_EMAIL_DOMAINS).split(",") if d.strip())
    return SamlConfig(
        idp_entity_id=get(ENV_IDP_ENTITY_ID),
        idp_sso_url=_https(get(ENV_IDP_SSO_URL), ENV_IDP_SSO_URL),
        idp_certs=_split_pem(pem),
        sp_entity_id=get(ENV_SP_ENTITY_ID) or f"{app}/api/v1/auth/saml/metadata",
        acs_url=_https(get(ENV_ACS_URL) or f"{app}/api/v1/auth/saml/acs", ENV_ACS_URL),
        email_domains=domains,
    )


# --------------------------------------------------------------------------- #
# Outbound: metadata and the authentication request
# --------------------------------------------------------------------------- #


def _instant(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def new_request_id() -> str:
    # An NCName: SAML IDs may not start with a digit.
    return "_" + secrets.token_hex(20)


def authn_request(config: SamlConfig, request_id: str) -> str:
    return (
        '<samlp:AuthnRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID={quoteattr(request_id)} Version="2.0" IssueInstant="{_instant()}" '
        f'Destination={quoteattr(config.idp_sso_url)} '
        f'AssertionConsumerServiceURL={quoteattr(config.acs_url)} ProtocolBinding="{_POST}">'
        f"<saml:Issuer>{escape(config.sp_entity_id)}</saml:Issuer>"
        f'<samlp:NameIDPolicy Format="{_EMAIL_FORMAT}" AllowCreate="true"/>'
        "</samlp:AuthnRequest>"
    )


def redirect_url(config: SamlConfig, request_id: str) -> str:
    """HTTP-Redirect binding: raw DEFLATE, base64, then the query string."""
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    deflated = compressor.compress(authn_request(config, request_id).encode()) + compressor.flush()
    query = urllib.parse.urlencode({"SAMLRequest": base64.b64encode(deflated).decode()})
    separator = "&" if urllib.parse.urlsplit(config.idp_sso_url).query else "?"
    return f"{config.idp_sso_url}{separator}{query}"


def sp_metadata(config: SamlConfig) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" '
        f"entityID={quoteattr(config.sp_entity_id)}>"
        '<md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
        'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        f"<md:NameIDFormat>{_EMAIL_FORMAT}</md:NameIDFormat>"
        f'<md:AssertionConsumerService Binding="{_POST}" '
        f'Location={quoteattr(config.acs_url)} index="0" isDefault="true"/>'
        "</md:SPSSODescriptor></md:EntityDescriptor>"
    )


# --------------------------------------------------------------------------- #
# Outstanding requests: shared by every instance, single use
# --------------------------------------------------------------------------- #


class RequestStore:
    """Requests this deployment has sent and not yet seen answered.

    In the database, not in memory: the response can reach any instance, and
    an in-memory record only exists on the one that sent the request.
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS saml_requests (
            id TEXT PRIMARY KEY,
            created_at {REAL} NOT NULL,
            expires_at {REAL} NOT NULL
        )
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self._db.execute(self._db.schema(self._SCHEMA))

    def begin(self) -> str:
        request_id = new_request_id()
        now = time.time()
        self._db.execute("DELETE FROM saml_requests WHERE expires_at < ?", (now,))
        self._db.execute("INSERT INTO saml_requests (id, created_at, expires_at) VALUES (?, ?, ?)",
                         (request_id, now, now + REQUEST_TTL_SECONDS))
        return request_id

    def consume(self, request_id: str) -> bool:
        """True once, for a request that exists and hasn't expired."""
        if not request_id:
            return False
        with self._db.transaction() as tx:
            row = tx.query_one("SELECT expires_at FROM saml_requests WHERE id = ?", (request_id,))
            if row is not None:
                tx.execute("DELETE FROM saml_requests WHERE id = ?", (request_id,))
        return row is not None and row[0] >= time.time()


def create_request_store() -> RequestStore:
    if backend() in {"sqlite", "postgres"}:
        return RequestStore(create_database())
    return RequestStore(Database("sqlite", ":memory:"))


# --------------------------------------------------------------------------- #
# Inbound: validating the response
# --------------------------------------------------------------------------- #


_DATETIME = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})?$")


def _parse_time(value: Optional[str], what: str) -> float:
    match = _DATETIME.match((value or "").strip())
    if not match:
        raise SamlError(f"{what} is not a valid xs:dateTime")
    base, fraction, tz = match.groups()
    dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
    offset = 0
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        offset = sign * (int(tz[1:3]) * 3600 + int(tz[4:6]) * 60)
    return dt.replace(tzinfo=timezone.utc).timestamp() - offset + (float(fraction) if fraction else 0.0)


def _parse(xml: bytes):
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                             huge_tree=False, remove_comments=False)
    try:
        root = etree.fromstring(xml, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise SamlError("response is not well-formed XML") from exc
    if root.getroottree().docinfo.doctype:
        raise SamlError("response contains a DOCTYPE")
    return root


def _verify(config: SamlConfig, xml: bytes):
    """The element the signature covers, verified against a pinned certificate.

    With `x509_cert` given, signxml verifies with that certificate and ignores
    any certificate the message carries in KeyInfo (checked in its source,
    5.3). It also refuses a pinned certificate past its validity date, so an
    expired IdP certificate fails sign-in until the operator rotates it.
    """
    from signxml import SignatureConfiguration, SignatureMethod, XMLVerifier
    from signxml.exceptions import InvalidCertificate, InvalidInput, InvalidSignature

    expect = SignatureConfiguration(
        expect_references=1,
        signature_methods=frozenset({
            SignatureMethod.RSA_SHA256, SignatureMethod.RSA_SHA384, SignatureMethod.RSA_SHA512,
            SignatureMethod.ECDSA_SHA256, SignatureMethod.ECDSA_SHA384, SignatureMethod.ECDSA_SHA512,
        }),
        ignore_ambiguous_key_info=True,
    )
    last: Optional[Exception] = None
    for cert in config.idp_certs:
        try:
            return XMLVerifier().verify(xml, x509_cert=cert, expect_config=expect).signed_xml
        except (InvalidSignature, InvalidCertificate, InvalidInput, ValueError) as exc:
            last = exc
    raise SamlError(f"signature did not verify against the configured IdP certificate: {last}")


_SKEW = CLOCK_SKEW_SECONDS


def _expired(value: Optional[str], what: str, now: float) -> bool:
    """A missing NotOnOrAfter counts as expired: bearer assertions must end."""
    return not value or _parse_time(value, what) <= now - _SKEW


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def validate_response(config: SamlConfig, saml_response: str, expected_request_id: str,
                      now: Optional[float] = None) -> Tuple[str, str]:
    """Check a POSTed SAMLResponse. Returns (name_id, email) or raises SamlError."""
    now = time.time() if now is None else now
    if not saml_response or len(saml_response) > MAX_RESPONSE_BYTES * 4 // 3 + 4:
        raise SamlError("response missing or too large")
    try:
        xml = base64.b64decode(saml_response, validate=False)
    except (ValueError, TypeError) as exc:
        raise SamlError("response is not base64") from exc

    root = _parse(xml)
    if root.tag != f"{{{NS['samlp']}}}Response":
        raise SamlError("not a samlp:Response")
    status = root.find("samlp:Status/samlp:StatusCode", NS)
    if status is None or status.get("Value") != _SUCCESS:
        raise SamlError(f"IdP returned status {status.get('Value') if status is not None else 'none'}")
    if root.findall(".//saml:EncryptedAssertion", NS):
        raise SamlError("encrypted assertions are not supported")
    if len(root.findall(".//saml:Assertion", NS)) != 1:
        raise SamlError("response must contain exactly one assertion")

    signed = _verify(config, xml)
    if signed.tag == f"{{{NS['saml']}}}Assertion":
        assertion = signed
    elif signed.tag == f"{{{NS['samlp']}}}Response":
        found = signed.findall("saml:Assertion", NS)
        if len(found) != 1:
            raise SamlError("signed response must contain exactly one assertion")
        assertion = found[0]
    else:
        raise SamlError("the signature covers neither the response nor the assertion")

    # From here on, only `assertion` (the signed copy) is read.
    if _text(assertion.find("saml:Issuer", NS)) != config.idp_entity_id:
        raise SamlError("assertion issuer is not the configured IdP")

    conditions = assertion.find("saml:Conditions", NS)
    if conditions is None:
        raise SamlError("assertion has no Conditions")
    if conditions.get("NotBefore") and _parse_time(conditions.get("NotBefore"), "NotBefore") > now + _SKEW:
        raise SamlError("assertion is not valid yet")
    if _expired(conditions.get("NotOnOrAfter"), "NotOnOrAfter", now):
        raise SamlError("assertion has expired")
    audiences = [_text(a) for a in conditions.findall("saml:AudienceRestriction/saml:Audience", NS)]
    if config.sp_entity_id not in audiences:
        raise SamlError("assertion is not addressed to this service provider")

    subject = assertion.find("saml:Subject", NS)
    confirmed = False
    for sc in (subject.findall("saml:SubjectConfirmation", NS) if subject is not None else []):
        data = sc.find("saml:SubjectConfirmationData", NS)
        if sc.get("Method") != _BEARER or data is None:
            continue
        if data.get("Recipient") != config.acs_url:
            continue
        if _expired(data.get("NotOnOrAfter"), "SubjectConfirmationData NotOnOrAfter", now):
            continue
        if not expected_request_id or data.get("InResponseTo") != expected_request_id:
            continue
        confirmed = True
        break
    if not confirmed:
        raise SamlError("no bearer confirmation for this ACS, this request and the current time")

    name_id_el = subject.find("saml:NameID", NS)
    name_id = _text(name_id_el)
    email = ""
    if name_id_el is not None and (name_id_el.get("Format") == _EMAIL_FORMAT or "@" in name_id):
        email = name_id
    if not email:
        for attr in assertion.findall("saml:AttributeStatement/saml:Attribute", NS):
            if (attr.get("Name") or "").strip().lower() in _EMAIL_ATTRIBUTES:
                value = attr.find("saml:AttributeValue", NS)
                if value is not None and _text(value):
                    email = _text(value)
                    break
    try:
        email = validate_email(email)
    except ValueError as exc:
        raise SamlError("assertion carries no usable email address") from exc
    domain = email.rsplit("@", 1)[1]
    if domain not in config.email_domains:
        raise SamlError(f"the IdP is not trusted for {domain} addresses")
    return name_id or email, email
