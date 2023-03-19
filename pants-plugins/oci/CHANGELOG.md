# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

- **Adds support for empty base images** (`FROM scratch`)

  This change enables you to use `base=["//:empty"]` to start from a completely empty
  container. This can be useful to produce containers with statically linked binaries that require
  no runtime environment at all.

  Note that for Pants 2.14 you'll need to manually declare the base image: `oci_image_empty(name="empty")`.

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
