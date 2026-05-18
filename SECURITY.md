# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 2.x | ✅ Active development. Security fixes published as patch releases. |
| 1.x | ⚠️ Receives critical security fixes only. No new features. |
| < 1.0 | ❌ Unsupported. |

When v3.0 is released, v2.x will continue to receive security fixes for
12 months.

## Reporting a vulnerability

**Do not** open a public issue for security vulnerabilities.

Email: `security@TODO-set-this.example` *(placeholder — replace with the actual contact before publishing)*

Include:

- A description of the vulnerability and its potential impact
- Steps to reproduce, if known
- The affected version(s)
- Any proof-of-concept code or files (please use an encrypted channel
  if the disclosure is sensitive)
- Your name and affiliation (if you wish to be credited)

## Response timeline

- **48 hours**: acknowledgement of receipt.
- **7 days**: initial assessment and severity classification (CVSS 3.1).
- **30 days**: target for a patched release for critical and high-severity
  issues. Medium and low severity issues are addressed in the next
  scheduled release.

## Disclosure policy

We follow coordinated disclosure:

1. Reporter contacts us privately.
2. We confirm and assess.
3. We prepare a fix in a private branch.
4. We coordinate a release window with the reporter.
5. The fix is released and the advisory is published simultaneously.
6. A CVE is requested if appropriate.

Public disclosure happens *after* a patched version is available. We do
not embargo for unreasonable periods; if 90 days pass without a fix, the
reporter is free to disclose publicly.

## Scope

In scope:

- The library's published API and its documented guarantees.
- Dependencies pinned in `pyproject.toml`.
- The installation flow (e.g. malicious package on PyPI confusion).

Out of scope:

- Vulnerabilities in the user's filesystem, OS, or storage hardware.
- Misuse: e.g. `safety="best_effort"` on NFS leading to data loss is
  documented behaviour, not a vulnerability.
- Issues that require the attacker to already have write access to the
  files the library is operating on.
- Issues in unsupported versions (see table above).
- Vulnerabilities in optional/transitive dependencies; please report those
  to the upstream maintainer first.

## Acknowledgements

We thank security researchers who report vulnerabilities responsibly.
Credited reporters (with consent) will be listed in the advisory and in
the release notes.
