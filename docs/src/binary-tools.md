# Binary tool versions

The plugin defaults and download metadata were refreshed on 2026-10-16; crane
was added on 2026-10-21 and zstd on 2026-10-22. CMake and Ninja (cmake plugin)
and rustup and kache (cargo plugin) were added on 2026-11-04. Each known-version
entry pins both SHA-256 and byte size. Versions can be selected with the
corresponding subsystem's `--version` option.

| Tool | Default | Additional release lines covered |
| --- | --- | --- |
| kubectl | 1.37.1 | 1.36.5, 1.35.9, 1.34.12 |
| Kustomize | 5.8.3 | 5.7.1, 5.0.3, 4.5.7 |
| crane | 0.22.1 | Latest only |
| mdBook | 0.5.4 | Latest 0.4 (0.4.52), plus existing historical entries |
| Bitwarden CLI | 2026.10.0 | 2026.9.1, plus existing historical entries |
| zstd | 1.5.6-bcr2 | Latest only |
| CMake | 3.31.12 | None |
| Ninja | 1.13.2 | None |
| rustup | 1.28.2 | None |
| kache | 1.2.2 | None |

The OCI plugin uses crane to pull, publish, and mirror images. It writes packaged
images as OCI archives itself, without an external tool.

The cmake plugin runs Ninja only to configure a project; replaying the build
never runs it. The cargo plugin runs kache as `RUSTC_WRAPPER`, a compiler cache.
rustup-init is pinned by hash like the other tools, but the Rust toolchain it
installs is pinned only by exact version, from `[rustup].rust_version` or the
`channel` of `rust-toolchain.toml`. rustup checks the toolchain against the
hashes in Rust's own release manifest; this repository records none for it.

The cc plugin's clang-tidy and gcovr are Python tools, pinned by the lockfiles
bundled with the plugin, so they are not listed here.

## Kubernetes and Kustomize

For kubectl, record the latest patch of each currently maintained minor and
remove entries when that minor reaches EOL. Consult the official
[release and EOL dates](https://kubernetes.io/releases/) on every refresh.
Kubernetes 1.34 remains in the table until its EOL on 2026-10-27.

The selected kubectl must be within one minor of the cluster's API server,
according to the [version skew policy](https://kubernetes.io/releases/version-skew-policy/).
For example, use `--kubectl-tool-version=v1.35.9` for a cluster requiring that
client minor.

Standalone Kustomize has its own release versions. The Kustomize embedded in
kubectl is a separate dependency; see the upstream
[kubectl/Kustomize version mapping](https://github.com/kubernetes-sigs/kustomize#kubectl-integration).
The standalone table intentionally retains the final 4.x release and selected
5.x minors for compatibility. Inclusion in the table does not promise ongoing
upstream maintenance of an older release line.

## Binary providers and platforms

Kubernetes, Kustomize, crane, mdBook, Bitwarden, CMake, Ninja, rustup, and
kache use official release artifacts. crane comes from the
[go-containerregistry release](https://github.com/google/go-containerregistry/releases)
tarballs.
CMake comes from the [Kitware/CMake releases](https://github.com/Kitware/CMake/releases)
and Ninja from the [ninja-build/ninja releases](https://github.com/ninja-build/ninja/releases);
both publish one universal macOS artifact, used for x86-64 and ARM64 alike.
rustup-init comes from Rust's archive at `static.rust-lang.org/rustup/archive`,
and kache from the [kunobi-ninja/kache releases](https://github.com/kunobi-ninja/kache/releases),
whose Linux builds are statically linked against musl.
zstd's upstream publishes Windows binaries only, so its downloads use the static
builds from [aspect-build/zstd-prebuilt](https://github.com/aspect-build/zstd-prebuilt),
which Bazel's bazel-lib uses as well. The OCI plugin runs it to read
zstd-compressed `.deb` packages, such as Ubuntu's.

The refreshed cross-platform tools include Linux and macOS on x86-64 and ARM64.
Recent mdBook releases use native Apple Silicon binaries, while its retained
older releases keep their Intel/Rosetta download mapping.
mdBook 0.5 rejects the removed `[book].multilingual` setting; remove it from
existing book configurations when switching from 0.4.
Odin and odinfmt are unchanged by this refresh.

## Verification

The subsystem download tests cover every recorded version/platform pair and
exercise Pants' URL generation, hash/size verification, and archive extraction.
Run these after updating the tables:

- `pants-plugins/k8s/pants_backend_k8s/subsystem_test.py`
- `pants-plugins/kustomize/pants_backend_kustomize/subsystem_test.py`
- `pants-plugins/oci/pants_backend_oci/subsystem_test.py`
- `pants-plugins/mdbook/pants_backend_mdbook/subsystem_test.py`
- `pants-plugins/bitwarden/pants_backend_bitwarden/subsystem_test.py`
- `pants-plugins/cmake/pants_backend_cmake/subsystems/subsystems_test.py`
- `pants-plugins/cargo/pants_backend_cargo/subsystems_test.py`

Successful downloads do not establish
runtime compatibility on a different host architecture.
