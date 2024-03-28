# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
