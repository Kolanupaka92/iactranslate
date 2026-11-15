# 0043. Encryption at rest for uploaded inventory

**Status:** Accepted
**Date:** 2026-08-27

## Context

An RVTools export is hostnames, IP addresses, OS and patch levels, cluster and
datacenter topology for an entire enterprise estate. For an attacker that is a
**reconnaissance map** — plausibly the most valuable thing this service will ever
hold, and more immediately useful than most databases it might touch.

It was written to disk as plaintext. One stolen backup, snapshot or
mis-permissioned volume would leak every customer's attack surface at once. The
architecture review named this the largest actual security exposure in the
product, and it was correct.

## Decision

**AES-256-GCM with per-file key derivation.** Each file carries a random 16-byte
salt; the file key is derived from the master key with HKDF-SHA256 over that
salt, so no two files share a key and compromising one reveals nothing about the
others. GCM authenticates, so tampering is detected rather than parsed. The
header is bound as additional authenticated data, which stops an attacker
splicing salts between files to force key or nonce reuse.

    IACTE1 | salt (16) | nonce (12) | ciphertext+tag

**Optional, via an extra.** `cryptography` is a compiled dependency and this tool
sells partly on installing and running anywhere with no services, so encryption
is `pip install iactranslate[encryption]`, enabled by setting
`IACTRANSLATE_ENCRYPTION_KEY`.

**It fails closed, everywhere.** A configured-but-broken key — malformed base64,
wrong length, missing library — raises rather than falling back. Silently writing
plaintext while the operator believes encryption is on is the one outcome worse
than having no encryption at all, because they would stop looking.

**Reads are keyed off the file header, not off configuration**, so enabling
encryption is not a flag day: a workspace holding both plaintext and encrypted
uploads reads correctly. The reverse — a key removed while encrypted files remain
— raises a clear error rather than handing a parser ciphertext.

Files are opened `0600` at `os.open` time rather than chmod-ed afterwards, since
a file created world-readable and fixed later is readable exactly while the
sensitive bytes are landing in it.

## Consequences

**There is a transient plaintext window, and it is deliberate.** The `Source`
protocol is path-based — `detect(path)` sniffs headers and `parse(path)` hands
the path to pandas — so an encrypted upload must touch the filesystem in the
clear to be read at all. `_plaintext_upload()` is the single place that happens:
it writes a `0600` copy inside the project's own workspace (same volume, so
nothing spills onto a shared `/tmp` with different permissions and a different
retention policy) and removes it in a `finally`. One auditable window beats the
same exposure scattered across five call sites. Closing it entirely means
teaching every source to parse from a buffer, which is tracked and not done here.

**Uploads are now buffered rather than streamed to disk**, because the bytes are
encrypted as one authenticated blob. `MAX_UPLOAD_BYTES` already bounded this and
the chunk loop still aborts the moment the ceiling is crossed, so an oversized
upload is never fully buffered.

**What this protects and what it does not.** It protects data at rest — stolen
disks, backups, snapshots, a misconfigured volume — where the attacker gets bytes
but not the process environment holding the key. It does **not** protect against
an attacker who can read the running process, and it is not a KMS: the master key
lives in an environment variable, so rotation is manual and there is no
per-tenant key separation. Both are on the roadmap; claiming otherwise would be
the same kind of overstatement this ADR exists to remove.

Still open: generated artifacts (the Terraform project and ZIP, which also carry
hostnames and IPs) are not yet encrypted, and there is no retention policy or
right-to-erasure implementation.
