# 0094. An HTTPS listener only with a certificate

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-26

## Context

The first real AWS deployment (26 November 2026, a new account in us-east-2)
failed on the public load balancer: AWS refuses an HTTPS listener without a
certificate. Every generator for AWS created one, with the certificate a variable
that defaults to empty, so every AWS estate with a web tier planned cleanly and
failed when applied. CloudFormation's README told the reader to add a certificate
by hand; Terraform, Pulumi and CDK said nothing until AWS refused.

The listeners also used `ELBSecurityPolicy-2016-08`, which still allows TLS 1.0.

The same run found that Terraform's AWS provider can't read `aws login` sessions,
which is how AWS now tells new users to sign in.

## Decision

- **The HTTPS listener exists only when a certificate is set:** `count` in
  Terraform, an `if` in Pulumi, a condition on a parameter in CloudFormation and
  CDK. Without one, the load balancer serves HTTP, and Terraform's plan warns
  (`check "https_certificate"`). A `moved` block keeps an existing listener in
  place.
- **TLS policy `ELBSecurityPolicy-TLS13-1-2-2021-06`**: TLS 1.2 and 1.3 only.
- **The README says how to use `aws login` with Terraform:** a profile with
  `credential_process` that asks the AWS CLI for the session. No key is stored.
- The plan tests now supply a certificate, so the HTTPS listener is planned in CI;
  before, nothing exercised it.

## Consequences

- An estate deployed with a certificate keeps its listener; one without it now
  deploys, on HTTP.
- An existing listener's TLS policy changes in place on the next apply.
- The AWS deployment also stopped because the account is on AWS's Free plan, which
  allows only free-tier instance types. That is the account, not the code.
