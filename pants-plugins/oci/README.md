# OCI backend for Pants

This release targets Pants **2.31.0**. Install it with
`pants-backend-oci==0.10.0` in `[GLOBAL].plugins` in `pants.toml`.

![PyPI](https://img.shields.io/pypi/v/pants-backend-oci?label=Latest%20release)

> **Warning**
> This plugin is in development. No stability is guaranteed! Contributions welcome.

This is a backend implementing support for building OCI images in pants; running them, and publishing them to container registries. To do this, this plugin uses four different tools:

* [umoci](https://github.com/opencontainers/umoci) for manipulating OCI images
* [runc](https://github.com/opencontainers/runc) for exeuction
* [crane](https://github.com/google/go-containerregistry/tree/main/cmd/crane) for pulling, pushing and mirroring images
* [skopeo](https://github.com/containers/skopeo) for exporting the packaged OCI layout

## Tool versions

The defaults are umoci `v0.6.0`, crane `v0.22.1`, Skopeo `v1.24.1`, and runc `v1.5.2`.
umoci uses official Linux binaries and matching macOS builds from
[tgolsson/umoci-binary](https://github.com/tgolsson/umoci-binary/releases/tag/v0.6.0).
Skopeo binaries come from [lework/skopeo-binary](https://github.com/lework/skopeo-binary),
since the Skopeo project does not publish standalone binaries. Both tools cover
Linux/macOS on x86-64 and ARM64. runc is Linux-only, with `v1.4.3`, `v1.3.6`, and
`v1.2.9` also recorded. crane uses the official go-containerregistry release archives.
Use `[umoci].version`, `[crane].version`, `[skopeo].version`, or `[runc].version`
to select another recorded version. Stable umoci now supports macOS without
selecting the legacy pinned `nightly` version.

Registry credentials are read the way crane reads them, from the first of these that
exists: `~/.docker/config.json` (or `$DOCKER_CONFIG/config.json`) with its credential
helpers, then `$REGISTRY_AUTH_FILE`, then podman/skopeo's `containers/auth.json` under
`$XDG_RUNTIME_DIR` or `$XDG_CONFIG_HOME` (`~/.config`). A docker config therefore hides a
podman/skopeo login. Pulled images are fetched for
the target's `os`/`architecture`, defaulting to `linux` and the host architecture, and
Docker v2 manifests are converted to OCI.

Kustomize injection publishes locally built image dependencies to their configured
registry before inserting their image digest. An omitted registry tag defaults
to `latest`.

# Planned and missing features

* Currently there's no support for pulling tags, as that would break determinism
* Multi-platform SHA/.sig is untested/unsupported
* Container execution and in-container build steps require Linux (runc).

## Targets

There's seven targets currently implemented, of which six are generic:

* `oci_pull_image`
* `oci_pull_images`
* `oci_image_build`
* `oci_image_empty`
* `oci_build_layer`
* `oci_deb_layer`

And one with some special language semantics:

* `oci_python_image` - this is the same as `oci_image_build`, but will prefer to set the entrypoint to `.pex` files.

### `oci_pull_image`

Pull an image from a repository with a specific digest.

``` python
oci_pull_image(
    name="base-python",
    repository="docker.io/library/python",
    sha="b78b777208be08edd8f297035cdfbacddb45170ad778fd643c792ee045187e39"
)
```

| Argument      | Meaning                                          | Default value                                         |
|---------------|--------------------------------------------------|-------------------------------------------------------|
| `name`        | The target name                                  | Same as any other target, which is the directory name |
| `repository`  | Fully qualified repository name                  | **Required**                                          |
| `sha`         | The digest of the image, minus the @sha: prefix. | **Required**                                          |
| `anonymous`   | Whether to pull the image anonymously.           | `false`                                               |
| `decsription` | A description of the target                      |                                                       |
| `tags`        | List of tags                                     | `[]`                                                  |

### `oci_pull_images`

Pull multiple shas for an image, generating a target for each. In the below example, we'd get the targets `:python#slim` and `:python#buster`.

``` python
oci_pull_image(
    name="python",
    repository="docker.io/library/python",
    variants={
       "slim": "f8fbb2370c6314c806b2ddbec8d94375987e16bc122379bef979c6fc5e962920",
       "buster": "97c123c899c8c9ca46248f4002ec4173322e0a1086b386efefac163c64967ba2"
    }
)
```

| Argument      | Meaning                                      | Default value                                         |
|---------------|----------------------------------------------|-------------------------------------------------------|
| `name`        | The target name                              | Same as any other target, which is the directory name |
| `repository`  | Fully qualified repository name              | **Required**                                          |
| `variants`    | Dictionary with local tags to the remote sha | **Required**                                          |
| `anonymous`   | Whether to pull the image anonymously        | `false`                                               |
| `decsription` | A description of the target                  |                                                       |
| `tags`        | List of tags                                 | `[]`                                                  |

### `oci_build_image`

Build an image with the provided packages embedded.

``` python
oci_image_build(
    name="my-server",
    base=":python#slim",
    repository="my-registry.example.com/a-namespace/an-image",
    tag="latest",
    packages=[":my_pex"]
)
```

| Argument      | Meaning                                                                        | Default value                                         |
|---------------|--------------------------------------------------------------------------------|-------------------------------------------------------|
| `name`        | The target name                                                                | Same as any other target, which is the directory name |
| `base`        | The base image to use. Matches the `FROM` directive in a Dockerfile            | **Required**                                          |
| `packages`    | Packaged targets to include. The first element will be used as the entrypoint. | `[]`                                                  |
| `repository`  | Fully qualified repository name                                                | Required when publishing                              |
| `tag`         | Remote tag to use                                                              | Required when publishing                              |
| `decsription` | A description of the target                                                    |                                                       |
| `tags`        | List of tags                                                                   | `[]`                                                  |

### `oci_python_image`

Build a Python image with the provided packages embedded.

``` python
oci_python_image(
    name="my-server",
    base=":python#slim",
    repository="my-registry.example.com/a-namespace/an-image",
	main="/app/server/start.py",
    tag="latest",
    packages=[":my_pex"]
)
```

| Argument      | Meaning                                                                        | Default value                                         |
|---------------|--------------------------------------------------------------------------------|-------------------------------------------------------|
| `name`        | The target name                                                                | Same as any other target, which is the directory name |
| `base`        | The base image to use. Matches the `FROM` directive in a Dockerfile            | **Required**                                          |
| `packages`    | Packaged targets to include. The first element will be used as the entrypoint. | `[]`                                                  |
| `python_main` | The main file to run                                                           | The last `.pex` in the dependency list                |
| `repository`  | Fully qualified repository name                                                | Required when publishing                              |
| `tag`         | Remote tag to use                                                              | Required when publishing                              |
| `decsription` | A description of the target                                                    |                                                       |
| `tags`        | List of tags                                                                   | `[]`                                                  |

### `oci_image_empty`

An empty base image with no contents at all. This is declared as `//:empty` automatically, but you can use this to create new targets.

``` python
oci_image_empty(
    name="empty",
)
```

| Argument      | Meaning                                                                        | Default value                                         |
|---------------|--------------------------------------------------------------------------------|-------------------------------------------------------|
| `name`        | The target name                                                                | Same as any other target, which is the directory name |
| `decsription` | A description of the target                                                    |                                                       |
| `tags`        | List of tags                                                                   | `[]`                                                  |

### `oci_extract`

Extract one or more files from another container image.

``` python
oci_extract(
    name="my-app-binary"
	base=[":some-app"],
	outputs=['/usr/bin/local/the-app'],
)
```

| Argument      | Meaning                                                                        | Default value                                          |
|---------------|--------------------------------------------------------------------------------|--------------------------------------------------------|
| `name`        | The target name                                                                | Same as any other target, which is the directory name  |
| `outputs`     | Paths to capture into the built layer.                                         | `[]`                                                   |
| `exclude`     | Globs to not include in the output.                                            | `[]`                                                   |
| `decsription` | A description of the target                                                    |                                                        |
| `output_path` | The output path during `pants package`                                         | A variant generated from the target name and directory |
| `tags`        | List of tags                                                                   | `[]`                                                   |

### `oci_build_layer`

Run a command in an image, and capture the configured output into a layer artifact, that can be injected into other images. This matches the `COPY --from` workflows.

``` python
oci_build_layer(
    name="layer"
	base=[":rust-1-70"],
    packages=[":files"],
    env=['RUSTC_OPTS=...'],
	commands=['cd /my-package && cargo build --release'],
	outputs=['/my-package/target/release/my-package'],
)
```

| Argument      | Meaning                                                                        | Default value                                          |
|---------------|--------------------------------------------------------------------------------|--------------------------------------------------------|
| `name`        | The target name                                                                | Same as any other target, which is the directory name  |
| `commands`    | The commands to execute in the container                                       | `[]`                                                   |
| `packages`    | Packaged targets to include. The first element will be used as the entrypoint. | `[]`                                                   |
| `env`         | Environment variables to set. Does not support interpolation.                  | `[]`                                                   |
| `outputs`     | Paths to capture into the built layer.                                         | `[]`                                                   |
| `exclude`     | Globs to not include in the output.                                            | `[]`                                                   |
| `decsription` | A description of the target                                                    |                                                        |
| `output_path` | The output path during `pants package`                                         | A variant generated from the target name and directory |
| `tags`        | List of tags                                                                   | `[]`                                                   |

### `oci_deb_layer`

Build a layer from Debian `.deb` packages without running `dpkg`, `apt` or any maintainer scripts. Package contents are extracted into the layer with zeroed timestamps, and each package is recorded in `/var/lib/dpkg/status.d/<package>` (plus `<package>.md5sums`), the same layout distroless images use, so scanners such as Trivy, Syft and Grype see the packages. Add it to an image through `layers`.

Dependencies are not resolved: list every package the layer needs on top of the base image. Pin the packages to a [snapshot.debian.org](https://snapshot.debian.org) URL so the layer stays reproducible. Only Debian packages are supported for now; zstd-compressed packages (as used by Ubuntu) are rejected. A path shipped by more than one package must be identical in each (type, mode, ownership and content or link target), as `dpkg` requires; directories keep the metadata of the first package by name. Hardlinked files are compared by name, so two packages that ship the same hardlink pair but store it the other way around (which name is the file and which is the link) are rejected. Files with extended attributes (such as file capabilities) are rejected, since the layer can't carry them.

Things to be aware of:

* No maintainer scripts run, so nothing a `postinst` would do happens: no `ldconfig`, no `update-alternatives` links, no triggers, and no generated files such as `/etc/ssl/certs/ca-certificates.crt`.
* `dpkg` and `apt` inside the image read `/var/lib/dpkg/status`, not `status.d`, so they don't see these packages. A package that is also in the base image is reported twice by scanners.
* `merged_usr` defaults to `True`, which is right for Debian 12 (bookworm) and later. Set it to `False` for older bases where `/bin` and `/lib` are real directories.
* Two packages may not ship the same file.

``` python
file(
    name="jq_deb",
    source=http_source(
        url="https://snapshot.debian.org/archive/debian/20241001T000000Z/pool/main/j/jq/jq_1.6-2.1_amd64.deb",
        sha256="d1833fdd7a1340b59c71c223178940044fc41a8c8e28861bc03ab258d791d303",
        len=64932,
        filename="jq_1.6-2.1_amd64.deb",
    ),
)

oci_deb_layer(
    name="jq",
    debs=[":jq_deb", ":libjq1_deb", ":libonig5_deb"],
)

oci_image_build(
    name="image",
    base=[":debian#bookworm-20241016-slim"],
    layers=[":jq"],
    entrypoint="/usr/bin/jq",
)
```

| Argument      | Meaning                                                                                              | Default value                                          |
|---------------|------------------------------------------------------------------------------------------------------|--------------------------------------------------------|
| `name`        | The target name                                                                                      | Same as any other target, which is the directory name  |
| `debs`        | The `.deb` files to extract, usually `file` targets with an `http_source`.                           | Required                                               |
| `merged_usr`  | Write contents of `/bin`, `/sbin` and `/lib*` under `/usr`, for bases where those are symlinks.      | `True`                                                 |
| `decsription` | A description of the target                                                                          |                                                        |
| `output_path` | The output path during `pants package`                                                               | A variant generated from the target name and directory |
| `tags`        | List of tags                                                                                         | `[]`                                                   |
