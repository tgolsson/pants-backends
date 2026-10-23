# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

- `deb_layer` with `merged_usr=True` (the default) writes the `/bin -> usr/bin` style links for
  the directories it moves under `/usr`, so it works on an empty base. Before, an image built
  only from deb layers had no `/lib64` and its dynamic loader couldn't be found.
- Compress each layer once, in its own cached process, so assembly only links existing blobs.
  Editing one layer no longer re-compresses the unchanged ones, such as a large dependencies
  layer.
- Add `oci_locale_layer`, which builds a reproducible layer with only the selected compiled
  locales, such as `C.utf8` from `libc-bin`, without running `locale-gen` or `localedef`.
- **Breaking:** `pants package` writes an `oci_image_build` as a deterministic OCI archive,
  `<name>.tar`, instead of an `<name>.d` OCI layout directory. The archive holds only the
  image's blobs, names the image `repository:tag` and includes a Docker `manifest.json`, so
  `podman load` and `docker load` keep the tag. It is written by the plugin's Python, so skopeo
  is no longer downloaded and the `[skopeo]` options are removed.
- **Breaking:** Remove the unused `[umoci]` and `[runc]` tools and the `[oci].rootless`,
  `uid_map` and `gid_map` options; setting them in `pants.toml` or on the command line is now an
  error.
- Run scripted build steps (`commands=` on `oci_image_build` and `oci_build_layer`) and
  `oci_extract` through `podman build` (podman 4.0 or newer) with `--timestamp 0` instead of
  umoci and runc. A failing command or missing output now fails the build instead of producing
  an empty artifact, and a scripted image step adds one layer on top of the unchanged base
  layers. Layer and extract outputs keep the owners and modes they have in the image, globs in
  `outputs` keep their paths, and empty `outputs` is an error.
- Run images and OCI repl through user-installed rootless podman on Linux, using OCI archives
  and separate Pants-managed storage. Non-root ownership and groups are preserved. Remote
  podman and macOS execution are not supported yet.
- Align the Python OCI examples with a pinned Debian 12 / Python 3.11 base.
- Assemble images in Python instead of with umoci: adding layers, editing the config, creating
  the empty base image and tagging pulled images now run a small
  Python script in a sandboxed process. Each image is assembled in one process instead of
  several umoci calls per layer. Built image digests change once: layers are gzipped
  differently, history entries no longer carry stray quotes, and empty `User`/`WorkingDir`
  fields from the base config are kept.
- `oci_build_layer` adds a layer for each of its `dependencies`, not only the first.
- `oci_deb_layer` and `oci_cacerts_layer` read zstd-compressed packages, such as Ubuntu's, by
  piping them through a new `[zstd]` tool: a static zstd binary of about 1 MB downloaded from
  aspect-build/zstd-prebuilt, not a Python dependency. `oci_cacerts_layer` is now built in a
  process instead of in the rule.
- Add `oci_passwd_layer`, `oci_group_layer` and `oci_cacerts_layer`, which build `/etc/passwd`
  (with home directories), `/etc/group` and a CA bundle from the `ca-certificates` package as
  reproducible layers, without running `useradd` or `update-ca-certificates`.
- Add `user` to `oci_image_build`.
- Add `oci_deb_layer`, which builds a layer from `.deb` packages without running dpkg or
  maintainer scripts, recording each package in `/var/lib/dpkg/status.d/`. Package contents
  are streamed, files shipped identically by several packages are accepted, and extended
  attributes are rejected.
- `oci_deb_layer` includes the transitive closure of its `debs`, so it accepts the targets
  `deb_packages` from `pants-backend-distroless` generates from a Debian lockfile. A `file`
  target in `debs` that has its own `dependencies` now brings them into the layer too, and they
  must be `.deb` files as well. Packages for more than one architecture are rejected.
- Stop setting a layer target's entrypoint to a nested layer tarball.
- Pull, publish and mirror images with crane instead of skopeo. Credentials come from the
  docker config and its credential helpers when one exists, and only otherwise from
  `REGISTRY_AUTH_FILE` or podman/skopeo's `containers/auth.json`; a `podman login` alongside a
  docker config is no longer used.
- Pulled images are converted from Docker v2 to OCI in the rule; an omitted `os` now
  defaults to `linux`.
- `oci_mirror_image` now copies the whole manifest list and keeps the source digest,
  instead of a single re-encoded platform image.

## 0.10.0 - 2026-10-17

- Target Pants `2.31.0`.
- Require `pants-backend-kustomize>=0.7.0`.
- Replace remaining union-dispatched `Get` calls with polymorphic call-by-name rules.

- Default to umoci `v0.6.0`, using official Linux x86-64/ARM64 binaries and refreshed
  macOS x86-64/ARM64 builds from `tgolsson/umoci-binary`. Retain the existing pinned
  nightly and `v0.4.7` entries; CI examples now use the stable default.
- Default to Skopeo `v1.24.1` from `lework/skopeo-binary` for all four platforms;
  remove the old version entries and provider-routing branches.
- Default to runc `v1.5.2`; include `v1.4.3`, `v1.3.6`, and `v1.2.9` for Linux
  x86-64/ARM64, replacing the old `v1.1.4` entries.
- Fix Kustomize OCI injection to supply the image layout/reference when publishing
  local bundles and default an omitted registry tag to `latest`.
- Check every recorded binary download and cover local/remote Kustomize injection
  with regression tests that mock registry operations.

## 0.9.0 - 2026-10-16

- Now targets `pants` version `2.30`.
- Migrate rules to the Pants call-by-name API, including the OCI mirror plugin.

## 0.8.1 - 2025-05-15

- Add `pants_backend_oci.utility.mirror` plugin to mirror images between repositories.

## 0.8.0 - 2025-05-14

- New target: `oci_extract`. Allows pulling out individual files from another image; without executing the image itself.
- Now targets `pants` version `2.24`.

## 0.7.0 - 2024-09-19

- Now targets `pants` version `2.22`.

## 0.6.2 - 2024-09-19

- Adds `[oci].unsafe_tar_ignore_file_changed` flag that will set `--warning=no-file-changed`
  when invoking tar

## 0.6.1 - 2024-03-28

- Fix a bug where the plugin would try to publish OCI images without a repository.
- Will now default to "latest" as a tag if none is specified.

## 0.6.0 - 2024-03-21

- Adding layers to an image is changing. As a preparatory step, there's now an `oci_layer` target. Add
  these to the `layers` field of an `oci_image_build`, instead of dependencies. These will process a bit
  better, and gives more control over what goes into each layer.
- Mac support has improved. In order to provide determinism, the `gtar` (GNU tar) binary has to be available.
- Both umoci and skopeo are now supported on Mac M1 and x86_64. This plugin does not support image run steps
  on Mac still.
- When pulling multi-arch images you can now specify `os` and `architecture` on the `oci_pull_image` target
- Fix a bug where OCI layer building would fail if no dependencies were specified

## 0.5.0 - 2023-11-19

- Fix a crash when image pulling fails
- Add `nightly` version for `umoci` with experimental support for Mac
- Add `v1.13.3` version for `skopeo` with experimental support for Mac

## 0.4.0 - 2023-06-18

- Improved support for very large layers > 2GB. A lot of layers will now be compressed in
  transit. This adds some overhead later when injecting them into image, but fixes some crashes
  inside Pants.

- **Improved support for multi-stage builds** (`COPY --from`)

  There is now support for building artifacts in one container and copying them into a new
  container. To do this, use `oci_build_layer`, and configure the output files and directories. The
  files and data will be inserted verbatim into the downstream container.

- **Adds support for empty base images** (`FROM scratch`)

  This change enables you to use `base=["//:empty"]` to start from a completely empty
  container. This can be useful to produce containers with statically linked binaries that require
  no runtime environment at all.


  - To change the target name, set `[oci].empty_image_target` in `pants.toml`.

## 0.3.1 - 2023-03-16

- Handle files when building layers

## 0.3.0 - 2023-03-14

* [breaking] Change output format when publishing

## 0.2.0 - 2023-02-10

* [bugfix] Ensure layer tars has no metadata or user information
* [bugfix] Properly pass args to runc
* [bugfix] Fix log output in package_oci_image
* [feature] Add proper metadata where possible
* [feature] Add support for anonymous image pulling (`anonymous=true`)
* [feature] Add support for passing image SHA along to Kustomize
* [feature] Add `python_image_target` which will set entrypoint appropriately
* [feature] Forward PATH, HOME, XDG_RUNTIME_DIR env variables to publish step to make credHelpers work

## [0.1.1] - 2022-11-17

* Move to new repo
* Compatibility fixes for 2.15.0a0

## [0.1.0] - 2022-11-13

Initial release.
