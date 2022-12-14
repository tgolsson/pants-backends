# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

* [bugfix] Ensure layer tars has no metadata or user information
* [bugfix] Properly pass args to runc
* [feature] Add proper metadata where possible
* [feature] Add support for anonymous image pulling (`anonymous=true`)
* [feature] Add support for passing image SHA along to Kustomize
* [feature] Add `python_image_target` which will set entrypoint appropriately

## [0.1.1] - 2022-11-17

* Move to new repo
* Compatibility fixes for 2.15.0a0

## [0.1.0] - 2022-11-13

Initial release.
