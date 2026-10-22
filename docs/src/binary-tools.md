# Binary tool versions

The plugin defaults and download metadata were refreshed on 2026-10-16; crane
was added on 2026-10-21 and zstd on 2026-10-22. Each known-version entry pins both SHA-256 and byte size. Versions can be selected with
the corresponding subsystem's `--version` option.

| Tool | Default | Additional release lines covered |
| --- | --- | --- |
| kubectl | 1.37.1 | 1.36.5, 1.35.9, 1.34.12 |
| Kustomize | 5.8.3 | 5.7.1, 5.0.3, 4.5.7 |
| crane | 0.22.1 | Latest only |
| runc | 1.5.2 | 1.4.3, 1.3.6, 1.2.9 |
| mdBook | 0.5.4 | Latest 0.4 (0.4.52), plus existing historical entries |
| Bitwarden CLI | 2026.10.0 | 2026.9.1, plus existing historical entries |
| umoci | 0.6.0 | Existing pinned nightly and 0.4.7 entries |
| zstd | 1.5.6-bcr2 | Latest only |

The OCI plugin uses crane to pull, publish, and mirror images. It writes packaged
images as OCI archives itself, without an external tool.

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

Kubernetes, Kustomize, crane, runc, mdBook, and Bitwarden use official release
artifacts. crane comes from the
[go-containerregistry release](https://github.com/google/go-containerregistry/releases)
tarballs.
zstd's upstream publishes Windows binaries only, so its downloads use the static
builds from [aspect-build/zstd-prebuilt](https://github.com/aspect-build/zstd-prebuilt),
which Bazel's bazel-lib uses as well. The OCI plugin runs it to read
zstd-compressed `.deb` packages, such as Ubuntu's.

umoci uses official Linux artifacts, including ARM64. macOS builds come from
[tgolsson/umoci-binary](https://github.com/tgolsson/umoci-binary), built from the
matching upstream release. The legacy `nightly` option still identifies the
existing pinned commit, rather than a moving download.

The refreshed cross-platform tools include Linux and macOS on x86-64 and ARM64;
runc is Linux-only. Recent mdBook releases use native Apple Silicon binaries,
while its retained older releases keep their Intel/Rosetta download mapping.
mdBook 0.5 rejects the removed `[book].multilingual` setting; remove it from
existing book configurations when switching from 0.4.
Odin and odinfmt are unchanged by this refresh.

## Verification

The subsystem download tests cover every recorded version/platform pair and
exercise Pants' URL generation, hash/size verification, and archive extraction.
Run the five `subsystem_test.py` files under k8s, kustomize, oci, mdbook, and
bitwarden after updating the tables. Successful downloads do not establish
runtime compatibility on a different host architecture; macOS umoci builds are
also smoke-tested on native GitHub runners before publishing.
