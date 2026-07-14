# Security policy

## Supported versions

This project is pre-1.0. Security fixes are made on `main` and released in the
latest tagged version. Older container-image tags remain immutable for
reproducibility but are not maintained with backported fixes.

## Reporting a vulnerability

Please use GitHub's
[private vulnerability reporting](https://github.com/danielmcquillen/purelms-interactive-tasks/security/advisories/new).
Do not include credentials, tokens, private learner data, exploit details, or
other sensitive material in a public issue or pull request.

Include the affected backend or frontend, release tag or image digest,
reproduction conditions, and expected impact. You should receive an initial
acknowledgement within seven days. A validated report will be fixed and
released before public disclosure whenever practical.

## Repository and release hygiene

- Never commit secrets, private keys, service-account JSON, `.env` files, or
  production data. GitHub secret scanning and push protection are enabled.
- `.allowed_signers` contains public SSH keys only. Keep key comments free of
  workstation hostnames and local account names.
- Official backend images are built from signed tags, published with SBOMs and
  GitHub build-provenance attestations, and deployed by immutable digest. See
  [RELEASING.md](RELEASING.md).
- If a signing key or credential may be compromised, revoke or rotate it
  immediately, remove its public key from `.allowed_signers`, and publish a new
  release. Existing immutable artifacts should be treated as suspect until
  their provenance has been reviewed.
