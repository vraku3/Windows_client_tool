# Public release signing implementation plan

> Execute inline with superpowers:executing-plans. The requested codex-rescue delegation tool is unavailable; do not substitute ordinary agents.

**Goal:** Prepare WinClientTool for signing under the user's validated personal identity through a trusted provider, without purchasing a certificate or changing Windows trust settings.

**Architecture:** Replace the legacy concatenated SignTool arguments with store-backed certificate selection and strict release checks. A nonsecret local profile pins the certificate and publisher. Signing creates a hash manifest only after both artifacts have verified signatures and timestamps.

**Tech stack:** Windows PowerShell 5.1, Windows SDK SignTool, pytest subprocess regression tests.

**Constraints:** Preserve existing uncommitted app fixes. No app build, launch, system tweak, certificate import/removal, identity submission or payment in this task. Production signing needs a provider-issued certificate; local chain validation alone cannot prove public trust or enterprise policy acceptance. Stop before deployment if signing fails.

- [ ] Add regression tests for native argument boundaries, certificate refusal before signing, and timestamp/signature verification.
- [ ] Implement store-backed release signing in tools/sign_build.ps1; no PFX password on the command line. Keep sign_build_local.ps1 available only for explicitly requested development signing.
- [ ] Add example profile and onboarding/release instructions; update AGENTS.md with the user's new production preference.
- [ ] Run focused tests, PowerShell parser and read-only missing-profile preflight. Review the diff and report remaining vendor enrollment steps.

**Provider recommendation:** Certum Standard Code Signing in the Cloud, individual validation, subject to vendor acceptance of Romanian identity/address and the final checkout total. Current shop starting price EUR 209; not a guaranteed tax-inclusive quote. SSL.com IV + eSigner is an alternative with certificate and subscription fees. Do not purchase until the user accepts the actual vendor offer.

**Superseded by user preference:** The user explicitly requested no spending. Paid enrollment is canceled; no account, order, payment or identity submission was made. Do not purchase a certificate or resume vendor onboarding. The signing script remains optional tooling, not an active deployment workflow. No new signature or deployment was performed in this task.
