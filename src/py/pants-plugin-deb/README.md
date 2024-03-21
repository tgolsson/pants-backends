# Debian package provider for Pants

![PyPI](https://img.shields.io/pypi/v/pants-plugin-deb?label=Latest%20release)

This is a plugin for Pants to provide debian packages for container builds (primarily). Bazel users may be familiar with the similar package [`rules_debian_packages`](https://github.com/bazel-contrib/rules_debian_packages).

## Targets

There's only a single primary target available here, which behaves similar to `python_requirements`:

* `deb_lockfile`

| Argument       | Meaning                                               | Default value                                         |
|----------------|-------------------------------------------------------|-------------------------------------------------------|
| `name`         | The target name                                       | Same as any other target, which is the directory name |
| `packages`     | Debian packages to include.                           | `[]`                                                  |
| `snapshot`     | Which Debian snapshot to use (e.g. 20240101T090148Z). | Required.                                             |
| `release`      | Which Debian release to use (bullseye, etc).          | Required.                                             |
| `architecture` | Which architecture to target.                         | `amd64` or `arm64` depending on current platform.     |
| `pools`        | Which pools to use                                    | `["main", "contrib"]`                                 |


``` python
deb_lockfile(
    name="bookworm-dependencies",
	packages=["python3.11", "jq"],
	release="bookworm",
	pools=["main", "contrib", "non-free-firmware"],
	architecture="amd64"
)
```
