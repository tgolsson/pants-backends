# OCI backend for Pants

![PyPI](https://img.shields.io/pypi/v/pants-backend-oci?label=Latest%20release)

> **Warning**
> This plugin is in development. No stability is guaranteed! Contributions welcome.

This is a backend implementing support for building OCI images in pants; running them, and publishing them to container registries. To do this, this plugin uses three different tools:

* [umoci](https://github.com/opencontainers/umoci) for manipulating OCI images
* [runc](https://github.com/opencontainers/runc) for exeuction
* [skopeo](https://github.com/containers/skopeo) for pulling and pushing images

# Planned and missing features

* Currently there's no support for pulling tags, as that would break determinism
* Multi-platform SHA/.sig is untested/unsupported
* skopeo doesn't support MacOS, preventing pulling and pushing images.
* No "in-container" build steps

## Targets

There's six targets currently implemented, of which five are generic:

* `oci_pull_image`
* `oci_pull_images`
* `oci_image_build`
* `oci_image_empty`
* `oci_build_layer`

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
