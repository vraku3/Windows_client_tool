# Optional provider signing tooling

The user chose **no spending** on 2026-10-31. Paid certificate enrollment is canceled. No certificate was purchased or provisioned. This document describes optional tooling only; it is not authorization to acquire a service or change certificate stores.

`tools/sign_build.ps1` can sign both existing `dist` executable formats when a suitable Code Signing certificate is already available through Windows CurrentUser\My and its provider middleware. The script does not build, deploy, enroll, import trust, or manage private keys. The former command-line PFX/password interface has been replaced by a nonsecret local profile.

Copy `tools/signing.example.json` to ignored `tools/signing.local.json` only after a certificate is available. Enter its SHA-1 thumbprint (certificate selector, not the file digest), exact Subject, and the provider's RFC3161 timestamp URL. Do not store passwords, tokens or private keys in either file.

```powershell
powershell -NoProfile -File tools/sign_build.ps1 -CheckOnly
powershell -NoProfile -File tools/sign_build.ps1
```

Preflight checks identity, validity, Code Signing usage, private-key association, certificate chain with online revocation, SignTool availability, and both build paths. It cannot prove that the cloud/token session can actually sign until a signing operation is attempted. A successful signing run verifies both signatures and timestamps before writing `dist/signing-manifest.json`. A failed run may leave one executable signed; do not deploy partial results. Existing success manifests are removed before signing starts.

Certificate chain validation uses the local Windows trust environment. It does not establish that a certificate is publicly trusted everywhere or accepted by an employer. Company-issued certificates can be useful where IT explicitly trusts the issuing CA and permits the application. Self-signed leaf certificates are refused by this release script; `sign_build_local.ps1` remains separate development tooling and does not satisfy Smart App Control's trusted-provider requirement.

No free universal bypass is supplied. An unsigned or locally signed portable application remains subject to Smart App Control, SmartScreen and employer App Control policies. Do not disable protections or import a personal certificate on managed devices to bypass these policies. IT approval may cover the executable and its bundled DLL/PYD dependencies; the executable signature alone is not a guarantee. Runtime tests must still use one elevated instance after closing the old app.

References checked 2026-10-31:

- [Microsoft signing options](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options)
- [Microsoft Smart App Control signing requirements](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/code-signing-for-smart-app-control)
- [Certum cloud SignTool instructions](https://support.certum.eu/en/signing-the-code-using-tools-like-signtool-and-jarsigner-instruction/)
