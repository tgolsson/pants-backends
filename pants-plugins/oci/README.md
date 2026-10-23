# OCI backend for Pants

This release targets Pants **2.31.0**. Install it with
`pants-backend-oci==0.10.0` in `[GLOBAL].plugins` in `pants.toml`.

![PyPI](https://img.shields.io/pypi/v/pants-backend-oci?label=Latest%20release)

> **Warning**
> This plugin is in development. No stability is guaranteed! Contributions welcome.

This is a backend implementing support for building OCI images in pants; running them, and publishing them to container registries. Images are assembled by the plugin itself: layers, config, manifest and index are written by a small Python script run in a sandboxed process, deterministically and without any container tool. Beyond that, this plugin uses these tools:

* [podman](https://podman.io/docs/installation), installed by the user, for `pants run`, OCI repl and scripted build steps
* [crane](https://github.com/google/go-containerregistry/tree/main/cmd/crane) for pulling, pushing and mirroring images

`pants package` writes each `oci_image_build` to `dist/` as an OCI archive, `<name>.tar`, with the
image named `repository:tag`. Load it with `podman load -i <archive>` or `docker load -i <archive>`.

## Running images

Install [podman](https://podman.io/docs/installation) on Linux and configure rootless execution,
including `/etc/subuid` and `/etc/subgid` mappings for your user. Run Pants as an unprivileged user.
Declarative packaging and registry publishing do not require podman; scripted build steps do
(see below).

`pants run examples/oci/debian:image` runs the image's entrypoint and default command.
Arguments after `--` replace its command arguments while keeping the entrypoint, for example
`pants run examples/oci/debian:image -- --help`. `terminal=True` allocates a TTY;
`pants repl --shell=oci <image-target>` also attaches stdin. Repl runs the image's configured
command, so select an image whose command provides the interaction you need.

Podman uses a dedicated `oci_podman/v1` directory under Pants' named-cache directory, with
private runtime and temporary directories under `/tmp` and the portable `vfs` storage driver.
Runtime directories are named by user and a hash of the store path to keep podman socket paths short. Exited containers
are removed; imported images and layers remain cached across runs. This store can grow and is
separate from your default podman store. Its files belong to your subordinate user ids, so clear
it with `podman unshare rm -rf <named-caches-dir>/oci_podman`. The `[oci].rootless`, `uid_map`
and `gid_map` options are no longer used; podman manages its own rootless mappings.
Remote connections (`CONTAINER_HOST` / `CONTAINER_CONNECTION`) are rejected. macOS support
requires a separate load/run path through `podman machine` and is deferred.

## Scripted build steps

`commands=` on `oci_image_build` and `oci_build_layer` runs a command inside the image. It is an
escape hatch: everything else is built declaratively, without running anything, and is
reproducible. A scripted step is only as reproducible as its command.

Scripted steps run through `podman build`, from the image as an OCI archive and with
`--timestamp 0`, as root inside the container. They need the same local, rootless Linux podman as
`pants run` and share its store. The command runs through `[oci].command_shell`
(`/bin/sh -c` by default), so the base image must have that shell; distroless images don't, and
the step then fails. Network access follows podman's defaults.

- `oci_build_layer` keeps only its `outputs` and packages them as a layer tarball for other images,
  with the owners and modes they have in the image and zeroed timestamps. An output may be a glob,
  matched one path segment per segment (`usr/lib/*/libfoo.so`); matches keep their paths.
- `oci_image_build` with `commands=` adds what the command changed as one more layer on top of
  the image's layers, which are kept as they are.
- `oci_extract` copies its `outputs` out of an image without running anything, but still reads
  the image through podman.

A failing command or a missing output fails the build. `examples/oci/scripted` shows all three.

## Tool versions

The defaults are crane `v0.22.1` and zstd `v1.5.6-bcr2`. crane uses the official
go-containerregistry release archives.
zstd, used by `oci_deb_layer`, `oci_cacerts_layer` and `oci_locale_layer` to read zstd-compressed packages such as
Ubuntu's, comes from the static builds of [aspect-build/zstd-prebuilt](https://github.com/aspect-build/zstd-prebuilt)
for Linux/macOS on x86-64 and ARM64. It is downloaded like the other tools, not installed as a Python
dependency.
Use `[crane].version` or `[zstd].version` to select another recorded
version. The `[umoci]` and `[runc]` options are no longer used and will be removed.

Registry credentials are read the way crane reads them, from the first of these that
exists: `~/.docker/config.json` (or `$DOCKER_CONFIG/config.json`) with its credential
helpers, then `$REGISTRY_AUTH_FILE`, then podman's `containers/auth.json` under
`$XDG_RUNTIME_DIR` or `$XDG_CONFIG_HOME` (`~/.config`). A docker config therefore hides a
podman login. Pulled images are fetched for
the target's `os`/`architecture`, defaulting to `linux` and the host architecture, and
Docker v2 manifests are converted to OCI.

Kustomize injection publishes locally built image dependencies to their configured
registry before inserting their image digest. An omitted registry tag defaults
to `latest`.

# Planned and missing features

* Currently there's no support for pulling tags, as that would break determinism
* Multi-platform SHA/.sig is untested/unsupported
* Container execution and scripted build steps require local Linux podman. Remote podman and macOS `podman machine` execution are not supported yet.

## Targets

There's eleven targets currently implemented, of which ten are generic:

* `oci_pull_image`
* `oci_pull_images`
* `oci_image_build`
* `oci_image_empty`
* `oci_build_layer`
* `oci_deb_layer`
* `oci_passwd_layer`
* `oci_group_layer`
* `oci_cacerts_layer`
* `oci_locale_layer`

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
| `description` | A description of the target                      |                                                       |
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
| `description` | A description of the target                  |                                                       |
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
| `user`        | The user to run as: a name or uid, optionally `:` and a group name or gid. Not checked at build time; the runtime resolves names when the container starts. | The base image's user |
| `description` | A description of the target                                                    |                                                       |
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
| `description` | A description of the target                                                    |                                                       |
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
| `description` | A description of the target                                                    |                                                       |
| `tags`        | List of tags                                                                   | `[]`                                                  |

### `oci_extract`

Extract one or more files from another container image. Nothing is run, but the image is read
through podman; see [Scripted build steps](#scripted-build-steps).

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
| `description` | A description of the target                                                    |                                                        |
| `output_path` | The output path during `pants package`                                         | A variant generated from the target name and directory |
| `tags`        | List of tags                                                                   | `[]`                                                   |

### `oci_build_layer`

Run a command in an image, and capture the configured output into a layer artifact, that can be injected into other images. This matches the `COPY --from` workflows. The command runs through podman; see [Scripted build steps](#scripted-build-steps).

``` python
oci_build_layer(
    name="layer"
	base=[":rust-1-70"],
    packages=[":files"],
    env=['RUSTC_OPTS=...'],
	commands='cd /my-package && cargo build --release',
	outputs=['/my-package/target/release/my-package'],
)
```

| Argument      | Meaning                                                                        | Default value                                          |
|---------------|--------------------------------------------------------------------------------|--------------------------------------------------------|
| `name`        | The target name                                                                | Same as any other target, which is the directory name  |
| `commands`    | The command to run in the container, through `[oci].command_shell`              |                                                        |
| `packages`    | Packaged targets to include. The first element will be used as the entrypoint. | `[]`                                                   |
| `env`         | Environment variables to set. Does not support interpolation.                  | `[]`                                                   |
| `outputs`     | Paths to capture into the built layer.                                         | `[]`                                                   |
| `exclude`     | Globs to not include in the output.                                            | `[]`                                                   |
| `description` | A description of the target                                                    |                                                        |
| `output_path` | The output path during `pants package`                                         | A variant generated from the target name and directory |
| `tags`        | List of tags                                                                   | `[]`                                                   |

### `oci_deb_layer`

Build a layer from Debian `.deb` packages without running `dpkg`, `apt` or any maintainer scripts. Package contents are extracted into the layer with zeroed timestamps, and each package is recorded in `/var/lib/dpkg/status.d/<package>` (plus `<package>.md5sums`), the same layout distroless images use, so scanners such as Trivy, Syft and Grype see the packages. Add it to an image through `layers`.

The layer gets the transitive closure of the `debs` targets. To have apt dependencies resolved, use `deb_packages` from [`pants-backend-distroless`](https://pypi.org/project/pants-backend-distroless), which locks a package set against a snapshot and generates targets that depend on the packages they need. Otherwise list every package the layer needs on top of the base image as `file` targets, pinned to a [snapshot.debian.org](https://snapshot.debian.org) or [snapshot.ubuntu.com](https://snapshot.ubuntu.com) URL so the layer stays reproducible. Debian and Ubuntu packages are both supported; members compressed with gzip, xz or bzip2 are read directly, and zstd-compressed ones (as Ubuntu uses) are piped through the `zstd` tool. A path shipped by more than one package must be identical in each (type, mode, ownership and content or link target), as `dpkg` requires; directories keep the metadata of the first package by name. Hardlinked files are compared by name, so two packages that ship the same hardlink pair but store it the other way around (which name is the file and which is the link) are rejected. Files with extended attributes (such as file capabilities) are rejected, since the layer can't carry them. Packages for more than one architecture are rejected; `Architecture: all` packages go with any.

Things to be aware of:

* No maintainer scripts run, so nothing a `postinst` would do happens: no `ldconfig`, no `update-alternatives` links, no triggers, and no generated files such as `/etc/ssl/certs/ca-certificates.crt` (use `oci_cacerts_layer` for that one).
* `dpkg` and `apt` inside the image read `/var/lib/dpkg/status`, not `status.d`, so they don't see these packages. A package that is also in the base image is reported twice by scanners.
* `merged_usr` defaults to `True`, which is right for Debian 12 (bookworm) and later. Set it to `False` for older bases where `/bin` and `/lib` are real directories.

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
| `debs`        | The `.deb` files to extract, with their dependencies: `deb_packages` targets or `file` targets.      | Required                                               |
| `merged_usr`  | Write contents of `/bin`, `/sbin` and `/lib*` under `/usr`, for bases where those are symlinks.      | `True`                                                 |
| `description` | A description of the target                                                                          |                                                        |
| `output_path` | The output path during `pants package`                                                               | A variant generated from the target name and directory |
| `tags`        | List of tags                                                                                         | `[]`                                                   |

### `oci_passwd_layer` and `oci_group_layer`

Build layers with `/etc/passwd` and `/etc/group`, the declarative replacement for `useradd` and `groupadd` in a scripted build. `oci_passwd_layer` also creates each user's home directory, owned by the user with mode `0700`; homes of `/` and `/nonexistent` are never created. Lines are written in the order given, and the layers are reproducible. Set `user` on `oci_image_build` to run as one of the users.

Each file replaces the base image's, so on a Debian base also list `root` and any other users and groups the base needs. Layers apply in the order of `layers`, so put these after any layer that ships its own `/etc/passwd` or `/etc/group`, or that one wins. Only the files and home directories are written, not their parent directories, so the base image's `/etc`, `/home` and the like keep their ownership and modes; missing parents are created when the image is unpacked. No `/etc/shadow` is written; a distroless image doesn't need one to resolve users.

The two targets are independent, so nothing checks that a user's `gid` or a group's `members` exist in the other.

``` python
oci_passwd_layer(
    name="passwd",
    users=[
        {"name": "root", "uid": 0, "gid": 0, "home": "/root", "shell": "/bin/sh"},
        {"name": "nonroot", "uid": 65532, "gid": 65532},
    ],
)

oci_group_layer(
    name="group",
    groups=[
        {"name": "root", "gid": 0},
        {"name": "nonroot", "gid": 65532, "members": ["nonroot"]},
    ],
)

oci_image_build(
    name="image",
    base=[":debian#bookworm-20241016-slim"],
    layers=[":passwd", ":group"],
    user="nonroot",
)
```

| Argument      | Meaning                                                                                                    | Default value                                          |
|---------------|------------------------------------------------------------------------------------------------------------|--------------------------------------------------------|
| `name`        | The target name                                                                                            | Same as any other target, which is the directory name  |
| `users`       | `oci_passwd_layer`: dicts with `name`, `uid`, `gid`, and optionally `home`, `shell`, `gecos`, `create_home`. | Required                                               |
| `groups`      | `oci_group_layer`: dicts with `name`, `gid`, and optionally `members`, a list of user names.               | Required                                               |
| `description` | A description of the target                                                                                |                                                        |
| `output_path` | The output path during `pants package`                                                                     | A variant generated from the target name and directory |
| `tags`        | List of tags                                                                                               | `[]`                                                   |

A user's `home` defaults to `/home/<name>`, `shell` to `/sbin/nologin`, `gecos` to empty and `create_home` to `True`. Names must match `[a-z_][a-z0-9_.-]*`, names and ids must be unique, and no value may contain `:` or a newline.

### `oci_cacerts_layer`

Build a layer with the CA bundle `/etc/ssl/certs/ca-certificates.crt` from the `ca-certificates` package, without running `update-ca-certificates`: every certificate under `/usr/share/ca-certificates` is concatenated in path order, as the package's `postinst` and `update-ca-certificates` would. The package's copyright file is included too. Like distroless images, the layer has no per-certificate files or OpenSSL hash links in `/etc/ssl/certs`, so tools that look certificates up by hash in a directory rather than reading the bundle file don't find them.

`debs` is searched for the `ca-certificates` package, so it can point at a target generated by `deb_packages`; the packages it depends on are ignored. Debian's and Ubuntu's packages both work.

The bundle is reproducible: certificates are concatenated in a bytewise sort of their paths, so it depends only on the package. It is not byte-for-byte what Debian would produce, though: the package's `postinst` sorts the list by locale, so a scripted build can order the certificates differently, and `!` deselections in `/etc/ca-certificates.conf` and certificates added under `/usr/local/share/ca-certificates` are not supported.

``` python
oci_cacerts_layer(
    name="cacerts",
    debs=[":bookworm#amd64/ca-certificates"],
)
```

| Argument      | Meaning                                                                 | Default value                                          |
|---------------|-------------------------------------------------------------------------|--------------------------------------------------------|
| `name`        | The target name                                                         | Same as any other target, which is the directory name  |
| `debs`        | Targets bringing exactly one `ca-certificates` `.deb`.                  | Required                                               |
| `description` | A description of the target                                             |                                                        |
| `output_path` | The output path during `pants package`                                  | A variant generated from the target name and directory |
| `tags`        | List of tags                                                            | `[]`                                                   |

### `oci_locale_layer`

Build a layer with only the selected compiled locales under `/usr/lib/locale`, such as `C.utf8` from `libc-bin`, without the rest of the package and without running `locale-gen` or `localedef`. glibc only has the ASCII `C` and `POSIX` locales built in, so a program that sets `LANG=C.UTF-8` or calls `setlocale` with it needs these files, or it silently falls back to ASCII. Distroless images ship `C.utf8` the same way.

`debs` is searched for packages shipping the requested locales as directories under `/usr/lib/locale`, so it can point at a target generated by `deb_packages`. Each locale must be in exactly one package, and each package a locale comes from adds its copyright file. Locales are looked up like glibc does: by the name as given, then with its codeset normalized, so `C.UTF-8` selects `C.utf8`. `C` and `POSIX` are built into glibc and rejected. Since nothing is compiled, only locales a package ships compiled can be selected: `libc-bin` ships `C.utf8` on Debian and Ubuntu, and the error lists the locales the packages do ship. A `locale-archive` file, as `locale-gen` writes, is not read. Hardlinks between locales become regular files. The locale directories are written with mode 0755, but not `/usr/lib/locale` itself, so the base image's is kept.

``` python
oci_locale_layer(
    name="locales",
    debs=[":bookworm#amd64/libc-bin"],
    locales=["C.UTF-8"],
)
```

| Argument      | Meaning                                                                 | Default value                                          |
|---------------|-------------------------------------------------------------------------|--------------------------------------------------------|
| `name`        | The target name                                                         | Same as any other target, which is the directory name  |
| `debs`        | Targets bringing the `.deb`s that ship the locales.                     | Required                                               |
| `locales`     | The compiled locales to keep.                                           | `["C.utf8"]`                                           |
| `description` | A description of the target                                             |                                                        |
| `output_path` | The output path during `pants package`                                  | A variant generated from the target name and directory |
| `tags`        | List of tags                                                            | `[]`                                                   |
