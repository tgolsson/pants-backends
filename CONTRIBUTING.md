# Contributing

## Verifying a change

Run `bash pre-commit.sh` from the repo root; it rewrites files (`update-build-files`, `tailor`, `fix`, `fmt`) before
checking. At minimum, `pants fmt lint test ::` and `pants package ::` must pass. When touching plugin rules, also run:

```
pants package examples::
pants package docs:docs
```

OCI tests and packaging need `/etc/subuid` and `/etc/subgid` entries for your user.

## Upgrading Pants

Every plugin release targets exactly one Pants version, so a Pants bump is a batch release of all plugins. The steps
below use `X` for the new Pants version.

1. Pick `X` as the newest *final* patch release of the next minor (check
   `https://github.com/pantsbuild/pants/releases`; `rc` tags don't count). Read `docs/notes/<minor>.x.md` on the
   upstream `<minor>.x` branch and the release blog post. Note the launcher minimum, the interpreter Pants runs on,
   removed APIs, and new optional plugin hooks (for example 2.32's `PublishFieldSet.check_skip_request`), and check the
   hooks against our `PublishFieldSet`/`PackageFieldSet` subclasses.
2. Update your launcher if the notes raise its minimum: `./get-pants.sh` (or `SCIE_BOOT=update pants`), then
   `PANTS_BOOTSTRAP_VERSION=report pants`. CI installs the latest with `./get-pants.sh` and uses
   `pantsbuild/actions/init-pants@main`.
3. Before changing anything, run `pants package examples::` and keep the `BuiltOciImage` digests, to compare against
   after the bump.
4. Set `[GLOBAL].pants_version = "X"` in `pants.toml`.
5. If Pants now runs on a different Python, move everything that runs plugin code to it (`==3.N.*`):
   `[python].interpreter_constraints`, `[python.resolves_to_interpreter_constraints]` for `pants-plugins`, `pytest` and
   `black`, `twine`, and `[black]`/`[isort]` `interpreter_constraints`. `example-1` follows the Python of the
   examples' `gcr.io/distroless/python3-debian13` base (3.13), not Pants'. Interpreters come from the `python_build_standalone` provider
   backend, locally and in CI, so nothing installs Python; if the provider doesn't know the new version yet, set
   `[python-build-standalone-python-provider].known_python_versions`.
6. Check the tool pins in `TOOLS` in the root `BUILD` against the new Pants defaults (`default_requirements` in
   upstream's `pytest.py`, `ipython.py`, `isort/subsystem.py`, `black/subsystem.py`, `twine.py`), and raise any that
   don't support the new interpreter.
7. Regenerate lockfiles with `pants generate-lockfiles --resolve=pants-plugins --resolve=pytest ...`, naming
   `pants-plugins` (which pins `pantsbuild.pants` via `pants_requirements` in `pants-plugins/BUILD`) and every resolve
   whose interpreter constraints or requirements changed. A bare `pants generate-lockfiles` also upgrades `example-1`
   and `twine`, which changes the example image digests for no reason. With `[python].separate_lockfile_metadata_file`,
   each lockfile's metadata (interpreter constraints, input requirements) is in a sibling `<lockfile>.metadata`;
   commit both.
8. Fix API breakage and deprecation warnings (`pants list ::` prints them). Rules use call-by-name; CI sets
   `PANTS_DISABLE_GETS=1`, so a stray `Get` fails the build. This applies to odin too: it is unreleased but still built
   and tested. `pants_at_least()` in `examples/macros.py` exists for version-gated BUILD code, but nothing uses it today.
9. Update every `gha-cache-key` in `.github/workflows/main.yml` and `docs.yml` (`cache1-<job>-py<python>-pantsX`, one
   per job).
10. Update the Pants badge in the root `README.md`.
11. Refresh pinned binary tools: update `default_version` and `default_known_versions` (SHA-256 and byte size) in each
    plugin's `subsystem.py`, update the table and refresh date in `docs/src/binary-tools.md`, and note new defaults in
    the CHANGELOG entries below.
12. For each released plugin — bitwarden, distroless, k8s, kustomize, mdbook, oci, secrets (odin is unreleased, `skip_twine=True`;
    don't bump it):
    - bump `version` in `pants-plugins/<name>/pyproject.toml` to the new plugin version `V`;
    - bump `python_artifact(version=...)` in `pants-plugins/<name>/BUILD` to `V`;
    - update `pants-plugins/<name>/README.md`: "This release targets Pants **X**. Install it with
      `pants-backend-<name>==V`";
    - add a `## V - <YYYY-MM-DD>` entry to `pants-plugins/<name>/CHANGELOG.md`, starting with "Target Pants `X`."
    - if the plugin depends on another one, raise the floor in its `pyproject.toml` `dependencies` to the upstream's new
      version and add a "Require `<dep>>=<version>`." CHANGELOG line. The floors are bitwarden → `pants_backend_secrets`,
      kustomize → `pants_backend_k8s` and oci → `pants-backend-kustomize`.
13. Verify as described above, and check CI is green. Compare the example digests with step 3. A new Pex version alone
    changes every image that contains a pex (2.32's Pex 2.81.0 → 2.95.1 did); say so in the oci CHANGELOG.
14. Publish by hand, e.g. `pants publish pants-plugins/secrets:package`; the PyPI token is read from Bitwarden. Publish in
    dependency order: secrets, bitwarden; k8s, kustomize, oci; distroless and mdbook at any point.
