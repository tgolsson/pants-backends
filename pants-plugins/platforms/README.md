# pants-backend-platforms

A [Pants](https://www.pantsbuild.org) backend for building targets for more than one platform.
It has no build rules of its own: language backends (such as pants-backend-cmake) use it to
know which platforms to build and how to ask another backend for a dependency built for the
same platform. The C and C++ toolchain for each platform is in [pants-backend-cc](../cc).

**Status: alpha.** Works with Pants 2.31 and 2.33.

## Targets

```python
platform(name="linux-x86_64", os="linux", arch="x86_64")
platform(name="windows-x86_64", os="windows", arch="x86_64")
```

- `platform`: `os` (`linux`, `windows`, `macos`) and `arch` (`x86_64`, `aarch64`). Everything
  refers to a platform by its target name, so names are unique.
- The host is the `platform` matching the machine Pants runs on. When several match, choose
  with `[platforms].host`; when none does, there is an implicit `<os>-<arch>` platform.

## Choosing what to build

A target of a backend that supports platforms has a `platforms` field: the platforms it can be
built for, the host alone when unset.

```python
cmake_target(name="app", project=":project", platforms=["linux-x86_64", "windows-x86_64"])
```

`pants package` builds the platforms in that list that are the host or in
`[platforms].enabled`. The host's output keeps its usual path in `dist/`, and every other
platform's goes under `dist/<platform>/`:

```
pants --platforms-enabled="['windows-x86_64']" package ::
```

A target built for a platform has its dependencies built for the same platform, enabled or not.
A dependency whose `platforms` does not list it is an error naming both targets.

Enable extra platforms where the packages are published rather than in `pants.toml`: whatever
packages a target, such as a test's `runtime_package_dependencies` or an `archive`, gets every
enabled platform too.

## For backend authors

- Make the field set a `PlatformBuildFieldSet` member, which brings the `platforms` field, and
  register `UnionRule(PlatformBuildFieldSet, MyFieldSet)`. Its rule takes the field set and a
  `TargetPlatform` and returns a `PlatformBuild`: the files at the paths `pants package` writes
  them to for the host.
- `build_platform_dependencies(PlatformDependenciesRequest(address, targets, platform))` builds
  the members among `targets` for the platform and checks their `platforms`. A tool that runs
  during the build is built with `build_for_platform` for `KnownPlatforms.host`.
- The `package` rule returns `package_for_platforms(PackageForPlatformsRequest(field_set))`.
  Goals that run what they build, such as `run` and `test`, build for the host.
