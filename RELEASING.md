# Releasing interactive-task backend images

This repo publishes its backend container images the **same way
`validibot-validator-backends` does** — a signed git tag triggers a CI
build that produces a sigstore attestation + SBOM and pushes to GHCR (and,
optionally, a private Google Artifact Registry). One mental model across
both projects.

> **PureLMS uses its own GCP, keys, registry, and identity.** Nothing here
> is shared with validibot. The sigstore attestation is signed by *this
> repo's* GitHub Actions identity, and the optional GAR mirror targets
> *PureLMS's own* GCP project via *this repo's* GitHub variables/secrets.
> Setting up the GAR mirror does not touch, read, or depend on any
> validibot project.

## What a release ships

For each backend slug (`echo`, `energyplus_single_zone`,
`modelica_diagram`), a release produces:

1. **A signed git tag** on this repo, verifiable with `git verify-tag`
   against [`.allowed_signers`](.allowed_signers).
2. **A sigstore build-provenance attestation** on the image digest,
   verifiable with `gh attestation verify`. This is what a PureLMS
   deployment running `SIMULATION_IMAGE_POLICY=signed_digest` checks before
   it will admit the backend at `install_interactive_task` time.
3. **An SPDX SBOM**, both embedded in the image manifest and attached to
   the GitHub Release page.

The two provenance layers stack: the signed tag gates the CI run that
produces the attestation, so a verified attestation implies a verified
source commit.

## Where the images land

```
ghcr.io/<your-org>/purelms-itask-<slug>:vX.Y.Z
ghcr.io/<your-org>/purelms-itask-<slug>:latest
```

`<slug>` is the backend directory name with underscores turned to hyphens
(`energyplus_single_zone` → `purelms-itask-energyplus-single-zone`). When
the GAR mirror is configured, the same digest also lands at:

```
<region>-docker.pkg.dev/<purelms-project>/<purelms-repo>/purelms-itask-<slug>:vX.Y.Z
```

## One-time setup

### 1. Tag signing (required)

- Configure git to sign tags with your SSH key:
  ```bash
  git config --global gpg.format ssh
  git config --global user.signingkey ~/.ssh/id_ed25519.pub
  ```
- Add the **public** half of that key to [`.allowed_signers`](.allowed_signers)
  (one line, `identity ssh-ed25519 AAAA...`). CI refuses any tag not signed
  by a key listed there.

### 2. GHCR (required, zero config)

Publishing to GitHub Container Registry uses the workflow's ambient
`GITHUB_TOKEN`. Just make sure GitHub Actions is enabled on the repo.

### 3. PureLMS GAR mirror (optional)

Only if you want the images in PureLMS's private Artifact Registry for
Cloud Run Jobs. In **this repo's** Settings → Secrets and variables →
Actions, set — **all pointing at PureLMS's own GCP project, never
validibot's:**

| Kind | Name | Value |
|---|---|---|
| Variable | `GCP_PROJECT_ID` | PureLMS's GCP project id |
| Variable | `GCP_REGION` | e.g. `us-central1` |
| Variable | `GCP_GAR_REPOSITORY` | PureLMS's Artifact Registry repo name |
| Secret | `GCP_WORKLOAD_IDENTITY_PROVIDER` | PureLMS's WIF provider resource |
| Secret | `GCP_SERVICE_ACCOUNT_EMAIL` | PureLMS's push service account |

Leave them unset and the release is GHCR-only — the GAR step is skipped
cleanly.

## Cutting a release

1. Bump `version` in `pyproject.toml`, commit, and push to `main`.
2. Run:
   ```bash
   just release 0.2.0
   ```
   It checks the tree is clean, on `main`, in sync with origin, and that
   the version matches `pyproject.toml`; then it signs and pushes the
   `v0.2.0` tag. CI takes it from there.
3. Watch it: `gh run watch`.

## Verifying a release before you deploy

```bash
# Resolve the digest (install crane if needed).
DIGEST=$(crane digest ghcr.io/<your-org>/purelms-itask-energyplus-single-zone:v0.2.0)

# Verify the sigstore attestation against the digest — confirms the image
# was built by THIS repo's GitHub Actions, signed via OIDC.
gh attestation verify \
  "oci://ghcr.io/<your-org>/purelms-itask-energyplus-single-zone@$DIGEST" \
  --owner <your-org>
# Expected: "Verification succeeded!"
```

This is the same check the PureLMS LMS runs server-side (via
`verify_image_signature`) when `SIMULATION_IMAGE_POLICY=signed_digest` and
you register the backend with `manage.py install_interactive_task`. A
tampered or unsigned image is refused at registration.

## How this connects to the LMS

The LMS treats the **image digest** as the trust root for a run. Signing
adds provenance on top: `SIMULATION_IMAGE_POLICY=signed_digest` makes the
LMS verify the attestation before admitting a backend, so evidence-bearing
simulations can only run on images this repo's CI actually built. Until you
flip that policy, images still publish and run — just unverified — so you
can adopt signing without a flag day.
