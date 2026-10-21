# Contributing

## Verifying a change

Run `bash pre-commit.sh` from the repo root; it rewrites files (`update-build-files`, `tailor`, `fix`, `fmt`) before
checking. At minimum, `pants fmt lint test ::` and `pants package ::` must pass. When touching plugin rules, also run:

```
pants --filter-address-regex='^((?!multistage).)*$' package examples::
pants package docs:docs
```

OCI tests and packaging need `/etc/subuid` and `/etc/subgid` entries for your user.

## Upgrading Pants

Every plugin release targets exactly one Pants version, so a Pants bump is a batch release of all plugins. The steps
below use `X` for the new Pants version.

1. Set `[GLOBAL].pants_version = "X"` in `pants.toml`. Read the release notes and deprecations for the new minor.
2. Regenerate all lockfiles with `pants generate-lockfiles`. This updates `pants-plugins/pants.lock` (which pins
   `pantsbuild.pants` via `pants_requirements` in `pants-plugins/BUILD`), `locks/example-1.lock` and `locks/tools/*.lock`.
3. Fix API breakage and deprecation warnings. Rules use call-by-name; CI sets `PANTS_DISABLE_GETS=1`, so a stray `Get`
   fails the build. This applies to odin too: it is unreleased but still built and tested. `pants_at_least()` in
   `examples/macros.py` exists for version-gated BUILD code, but nothing uses it today.
4. Update the three `gha-cache-key` values in `.github/workflows/main.yml` (`cache1-...-pantsX`).
5. Update the Pants badge in the root `README.md`.
6. Refresh pinned binary tools: update `default_version` and `default_known_versions` (SHA-256 and byte size) in each
   plugin's `subsystem.py`, update the table and refresh date in `docs/src/binary-tools.md`, and note new defaults in
   the CHANGELOG entries below.
7. For each released plugin — bitwarden, distroless, k8s, kustomize, mdbook, oci, secrets (odin is unreleased, `skip_twine=True`;
   don't bump it):
   - bump `version` in `pants-plugins/<name>/pyproject.toml` to the new plugin version `V`;
   - bump `python_artifact(version=...)` in `pants-plugins/<name>/BUILD` to `V`;
   - update `pants-plugins/<name>/README.md`: "This release targets Pants **X**. Install it with
     `pants-backend-<name>==V`";
   - add a `## V - <YYYY-MM-DD>` entry to `pants-plugins/<name>/CHANGELOG.md`, starting with "Target Pants `X`."
   - if the plugin depends on another one, raise the floor in its `pyproject.toml` `dependencies` to the upstream's new
     version and add a "Require `<dep>>=<version>`." CHANGELOG line. The floors are bitwarden → `pants_backend_secrets`,
     kustomize → `pants_backend_k8s` and oci → `pants-backend-kustomize`.
8. Verify as described above, and check CI is green.
9. Publish by hand, e.g. `pants publish pants-plugins/secrets:package`; the PyPI token is read from Bitwarden. Publish in
   dependency order: secrets, bitwarden; k8s, kustomize, oci; distroless and mdbook at any point.
