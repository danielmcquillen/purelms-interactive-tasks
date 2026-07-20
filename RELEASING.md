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
4. **Validated immutable assets.** CI verifies every manifest asset hash and
   rejects FMUs whose embedded Linux binaries are not x86-64 before creating
   the GitHub Release.
5. **Version-honest task identities.** CI compares release inputs with the
   preceding tag. This is an aggregate release: every image receives the new
   repository release label, so every released task's manifest version must
   increase. This keeps each versioned provider route bound to one digest.
6. **Operator-readable OCI identity.** Every image records its repository
   release version, source revision, source repository, backend slug, and
   manifest task version. The inventory's shared-contract floor is installed
   exactly and recorded too, so rebuilding the tag cannot silently select a
   newer library. All external Python packages are constrained by the signed
   tag's `uv.lock`. The digest remains the cryptographic identity.

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
- Omit public-key comments that expose a workstation hostname or local account
  name. The principal, namespace restriction, key type, and public key are
  sufficient.

### 2. GHCR (required, zero config)

Publishing to GitHub Container Registry uses the workflow's ambient
`GITHUB_TOKEN`. Just make sure GitHub Actions is enabled on the repo. The image
source label links each package to this repository.

GitHub artifact attestations for this user-owned repository require the
repository and its packages to be public. The signed bundles are stored with
the OCI images and verified with `--bundle-from-oci`.

After the **first** release creates the three packages, confirm each is public:

1. Open your GitHub profile → **Packages**.
2. Open each `purelms-itask-*` package → **Package settings**.
3. Under **Danger Zone**, choose **Change visibility → Public**.

GHCR packages created under a personal account default to private. Public
visibility is required because the deployed LMS verifies the public GHCR twin
without a Docker credential helper. This is a one-time action per package; test
it by resolving a digest while logged out of GHCR.

### 3. PureLMS GAR mirror (optional)

Only if you want the images in PureLMS's private Artifact Registry for
Cloud Run Jobs and Services. The keyless setup recipe creates a repository-scoped Workload
Identity provider, a least-privilege publisher service account, and all five
GitHub Actions variables—no service-account key or GitHub secret is created:

```bash
just setup-release-gar \
  <github-owner>/purelms-interactive-tasks \
  <purelms-gcp-project-id> \
  <gcp-region> \
  <gar-repository>
```

The recipe uses GitHub's stable numeric repository and owner IDs in the WIF
condition, grants `roles/iam.workloadIdentityUser` only to that repository,
and grants `roles/artifactregistry.writer` only on the named GAR repository.
It sets these non-secret variables in **this repository**, all pointing at
PureLMS's own GCP project, never validibot's:

| Kind | Name | Value |
|---|---|---|
| Variable | `GCP_PROJECT_ID` | PureLMS's GCP project id |
| Variable | `GCP_REGION` | e.g. `us-central1` |
| Variable | `GCP_GAR_REPOSITORY` | PureLMS's Artifact Registry repo name |
| Variable | `GCP_WORKLOAD_IDENTITY_PROVIDER` | PureLMS's repository-scoped WIF provider resource |
| Variable | `GCP_SERVICE_ACCOUNT_EMAIL` | PureLMS's least-privilege image publisher |

Leave them unset and the release is GHCR-only — the GAR step is skipped
cleanly.

## Cutting a release

1. Bump `version` in `pyproject.toml`. Also bump every released task's manifest,
   `backend/__metadata__.py`, and Dockerfile `PURELMS_TASK_VERSION`; refresh
   `uv.lock`, commit, and push to `main`.
2. Run the complete local release gate:
   ```bash
   just release-preflight 0.2.3
   ```
   It validates immutable assets and version changes, runs lint and all tests,
   builds every frontend, and runs the real EnergyPlus/Modelica smoke suite as
   `linux/amd64`. It may create normal local build output but does not tag,
   publish, or mutate a deployment.
3. Run:
   ```bash
   just release 0.2.3
   ```
   It checks the tree is clean, on `main`, in sync with origin, and repeats the
   release preflight before it signs and pushes `v0.2.3`. CI repeats the checks
   before creating the release.
4. Watch it: `gh run watch`.

## Verifying a release before you deploy

```bash
# Resolve the digest (install crane if needed).
DIGEST=$(crane digest ghcr.io/<your-org>/purelms-itask-energyplus-single-zone:v0.2.3)

# Verify the sigstore attestation against the digest — confirms the image
# was built by THIS repo's GitHub Actions, signed via OIDC.
gh attestation verify \
  "oci://ghcr.io/<your-org>/purelms-itask-energyplus-single-zone@$DIGEST" \
  --bundle-from-oci \
  --repo <your-org>/purelms-interactive-tasks \
  --signer-workflow \
    <your-org>/purelms-interactive-tasks/.github/workflows/release.yml \
  --deny-self-hosted-runners
# Expected: "Verification succeeded!"
```

Cloud Run executes the private GAR mirror, but the LMS verifies this public
GHCR twin at the **same digest**. Because this is a personal repository, the
workflow stores the signed bundle with the GHCR image and verification uses
`--bundle-from-oci`. Resolving the public twin means the deployed management
job needs only `GH_TOKEN`, not a GAR Docker credential helper. The verified
bytes are identical because the release workflow mirrors the manifest without
rebuilding it.

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
