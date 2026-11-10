# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Changed

- The `debs` of `oci_deb_layer`, `oci_cacerts_layer` and `oci_locale_layer` also accept targets
  whose sources generate `.deb` files, like the `deb_package` targets of `pants-backend-distroless`,
  whose sources are now the `.deb` files of their locked closure.

### Fixed

- `pants help targets`, `help-all`, `help-advanced targets`, `help oci_passwd_layer` and
  `help oci_group_layer` no longer fail with `NameError: name 'PasswdEntry' is not defined` (or
  `'GroupEntry'`).

## 0.11.0rc0 - 2026-11-04

- Target Pants `2.32.x`.
- Require `pants-backend-kustomize>=0.8.0rc0`.

This release replaces umoci, runc and skopeo: images are assembled by the plugin's own Python,
scripted steps and `pants run` use podman (or Docker), and registries are reached with crane.
Read the breaking changes before upgrading.

### Breaking

- `pants package` writes an `oci_image_build` as a deterministic OCI archive, `<name>.tar`,
  instead of an `<name>.d` OCI layout directory. The archive holds only the image's blobs, names
  the image `repository:tag` and includes a Docker `manifest.json`, so `podman load` and
  `docker load` keep the tag. It is written by the plugin's Python, so skopeo is no longer
  downloaded and the `[skopeo]` options are removed. Set the new
  `[oci].package_output = "layout"` to get a layout directory, `<name>/`, instead (see Added).
- Remove the `[umoci]` and `[runc]` tools and the `[oci].rootless`, `uid_map` and `gid_map`
  options; setting them in `pants.toml` or on the command line is now an error.
- Scripted build steps (`commands=` on `oci_image_build` and `oci_build_layer`) and
  `oci_extract` run through `podman build` (podman 4.0 or newer, installed by the user, local
  rootless Linux) with `--timestamp 0` instead of umoci and runc. A failing command or missing
  output now fails the build instead of producing an empty artifact, and a scripted image step
  adds one layer on top of the unchanged base layers. Layer and extract outputs keep the owners
  and modes they have in the image, globs in `outputs` keep their paths, and empty `outputs` is
  an error. Declarative packaging and publishing need no podman.
- `pants run` and OCI repl run images through user-installed rootless podman, in separate
  Pants-managed storage, with non-root ownership and groups preserved; or through Docker with
  `[oci].runner = "docker"` (see Added).
- Image digests change once. diff_ids of layers whose contents are unchanged stay the same.
  - Images are assembled by the plugin's Python instead of umoci: history entries no longer
    carry stray quotes, and empty `User`/`WorkingDir` fields from the base config are kept.
  - Layers are gzipped pigz-style, in parallel fixed-size blocks, instead of as one stream. Blobs
    come out about 0.4% larger, and every layer and image digest changes.
  - The packaged-files layer (`layers/image_bundle.tar`) is written by the plugin's Python
    instead of the host `tar`: files are 0755 or 0644 by their executable bit (files of 512KB
    or more came out 0555 before), and hardlinked paths are stored as copies.
  - `oci_image_build` stacks `layers=` in the order they are listed, not in address order, so
    where two layers wrote the same path the wrong one could win. Images whose `layers` are not
    listed in address order get new digests.
  - Pants 2.32 bundles Pex 2.95.1 instead of 2.81.0, which packages the same `pex_binary`
    differently, so every image that contains a pex changes digest with no plugin change. Images
    without a pex are unaffected by this.
- Pull, publish and mirror images with crane instead of skopeo. Credentials come from the docker
  config and its credential helpers when one exists, and only otherwise from
  `REGISTRY_AUTH_FILE` or podman/skopeo's `containers/auth.json`; a `podman login` alongside a
  docker config is no longer used.
- The podman store moves from `oci_podman/v1` to `oci_podman/v2`, and podman's runtime state
  from private directories under `/tmp` to the runtime directory rootless podman uses without
  `XDG_RUNTIME_DIR` (`/run/user/<uid>`, else `/tmp/podman-run-<uid>`), so it stays out of build
  steps' cache keys. Bases are imported again once. Remove the old store and directories with
  `podman unshare rm -rf <named-caches-dir>/oci_podman/v1 /tmp/oci-podman-$(id -u)-*`. The README
  shows how to clear the store.

### Added

- `[oci].package_output = "layout"` makes `pants package` write an image as an OCI layout
  directory, `<name>/`, instead of an archive. The layout reuses the blobs already in the Pants
  store, so packaging, and `pants publish`, which packages every image it pushes, no longer
  write and store a multi-GB copy of a large image. The archive stays the default. See
  "Packaging as an OCI layout" in the README.
- `pants run` and OCI repl on macOS through `podman machine`, and on any
  platform through a named podman connection with the new `[oci].podman_connection` option. The
  image is loaded into the connection's store as `localhost/pants-oci-run:<digest>` and kept for
  later runs; a missing or stopped machine is reported with the command to fix it. Connections
  are tested in CI against a Linux podman service; macOS was validated manually on an Apple
  silicon Mac. Scripted build steps still need local Linux podman. See "Running through podman machine"
  in the README.
- `[oci].runner = "docker"` runs `pants run`, OCI repl and scripted build steps with Docker
  instead of podman, for teams without podman. It needs Docker 23.0 or newer and the
  `pants.backend.docker` and `pants_backend_oci.docker` backends; Docker is found and configured
  by the Docker backend's `[docker]` options. podman stays the default. See "Running with Docker"
  in the README.
- `oci_deb_layer` builds a layer from `.deb` packages without running dpkg or maintainer scripts,
  recording each package in `/var/lib/dpkg/status.d/`. Package contents are streamed, files
  shipped identically by several packages are accepted, and extended attributes are rejected. It
  includes the transitive closure of its `debs`, so it accepts the targets `deb_packages` from
  `pants-backend-distroless` generates from a Debian lockfile; a `file` target in `debs` that has
  its own `dependencies` brings them into the layer too, and they must be `.deb` files as well.
  Packages for more than one architecture are rejected. With `merged_usr=True` (the default) it
  writes the `/bin -> usr/bin` style links for the directories it moves under `/usr`, so it works
  on an empty base: an image built only from deb layers used to have no `/lib64`, so its dynamic
  loader couldn't be found.
- `oci_deb_layer` and `oci_cacerts_layer` read zstd-compressed packages, such as Ubuntu's,
  through a new `[zstd]` tool: a static zstd binary of about 1 MB downloaded from
  aspect-build/zstd-prebuilt, not a Python dependency.
- `oci_passwd_layer`, `oci_group_layer` and `oci_cacerts_layer` build `/etc/passwd` (with home
  directories), `/etc/group` and a CA bundle from the `ca-certificates` package as reproducible
  layers, without running `useradd` or `update-ca-certificates`. Each is built in a sandboxed
  process; the passwd and group layers had been built in the rule, with the same digests.
- `oci_locale_layer` builds a reproducible layer with only the selected compiled locales, such as
  `C.utf8` from `libc-bin`, without running `locale-gen` or `localedef`.
- `user` on `oci_image_build`.
- `commands=` on `oci_image_build` and `oci_build_layer` also takes a list of strings, run as that
  argv without `[oci].command_shell`, so scripted steps work on bases with no shell, such as
  distroless ones. A string still runs through the shell. `examples/oci/multistage` uses it and
  packages again.
- New `deb-layer-overwrites` linter: `pants lint` fails when an `oci_deb_layer` in an
  `oci_image_build` replaces a regular file from a lower layer, such as the base image, with
  different content, naming the packages and versions involved. Identical files aren't reported.
  Each layer is indexed once, in its own cached process. Linting builds or pulls the base of every
  image with a deb layer.
- `[oci].layer_compression_level` (default 6) sets the gzip level of layers.
- `[oci].builder_python_version` and `[oci].builder_pbs_release` select the Python of the
  `oci-builder` executable (see Changed).

### Changed

- `[oci].unsafe_tar_ignore_file_changed` is deprecated and has no effect.
- Layers build much faster cold. A layer of files and packages is tarred and gzipped in one step,
  without capturing an uncompressed tar, and every layer is gzipped on up to 16 cores. On a
  1.5 GB layer, tar and gzip went from 79 s to 12 s. Each layer is compressed once, in its own
  cached process, so editing one layer no longer re-compresses unchanged ones, such as a large
  dependencies layer.
- Assembling an image runs in one sandboxed process and copies no layer blobs: it sees only the
  base's index, manifest and config and each layer's descriptor, and the blobs are linked into
  the layout afterwards. On a 3 GB image, the assemble step of an incremental rebuild went from
  25 s to under 0.1 s.
- The sandboxed builders run as one `oci-builder` executable, a scie built in the rules with pex
  from the plugin's builder scripts and a pinned python-build-standalone Python (3.11.17),
  instead of as scripts run by Pants' own Python at its absolute path under the user's home.
  Builder processes name nothing on the host, so their cache keys are the same on every machine
  and builds share remote-cache hits; only building `oci-builder` itself, once per machine, does
  not. Building it downloads that Python and pex's `science` tool from GitHub the first time. It
  unpacks once per machine into the new `oci_builder` named cache, `pants run` included, so
  builders need no home directory. Layer compression no longer depends on the Python Pants runs
  on. Podman and the Docker CLI look up the user's home themselves. The plugin registers Pants'
  pex rules for this. This changes no digest of its own.
- `pants run` with local podman runs the image straight from its layout and removes the image it
  imported when the run exits, instead of leaving the previous one dangling whenever another
  image is run. What an interrupted build step leaves in the podman store, its working
  container, is removed by the next run or build that finds the store otherwise unused; runs and
  build steps hold a shared lock on the store to tell. Failing to remove a built image is now
  reported.
- Scripted build steps rerun when the podman binary changes, instead of reusing layers cached with
  the old podman. They read podman's configuration from your home directory, like `pants run`,
  instead of from an empty private one. `pants run` no longer rejects `CONTAINER_HOST` /
  `CONTAINER_CONNECTION`: both it and build steps ignore them. Only `[oci].podman_connection`
  selects a connection, and only for `pants run` and repl; build steps always use local podman.
- Pulled images are converted from Docker v2 to OCI in the rule; an omitted `os` now defaults to
  `linux`.
- `oci_mirror_image` copies the whole manifest list and keeps the source digest, instead of a
  single re-encoded platform image, so a mirrored image's digest now matches its source.
- The README explains how rebuilding large images grows the Pants store, and suggests a lower
  `local_store_files_max_size_bytes`.

### Fixed

- Cold builds of large images no longer fail intermittently with GNU tar's "file changed as we
  read it" (tar before 1.35 exits 1 when Pants hardlinks the same large file into another sandbox
  while it is read): the packaged-files layer and the archive a podman connection loads are
  written by the plugin's Python instead of the host `tar`, so macOS no longer needs `gtar` for
  them.
- `oci_build_layer` adds a layer for each of its `dependencies`, not only the first.
- A layer target's entrypoint is no longer set to a nested layer tarball.

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
