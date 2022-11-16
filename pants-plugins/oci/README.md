# OCI backend for Pants

This is a backend implementing support for building OCI images in pants; running them, and publishing them to container registries. To do this, this plugin uses three different tools:

* [umoci](https://github.com/opencontainers/umoci) for manipulating OCI images
* [runc](https://github.com/opencontainers/runc) for exeuction
* [skopeo](https://github.com/containers/skopeo) for pulling and pushing images

# Planned and missing features

* Currently there's no support for pulling tags, as that would break determinism
* Multi-platform SHA/.sig is untested/unsupported
* skopeo doesn't support MacOS, preventing pulling and pushing images.
* No empty image base
* No "in-container" build steps

## Targets

There's three targets currently implemented:

* `oci_pull_image`
* `oci_pull_images`
* `oci_build_image`

There are also plans to support targets optimized for various languages.

### `oci_pull_image`

Pull an image from a repository with a specific digest.

``` python
oci_pull_image(
    name="base-python",
    repository="docker.io/library/python",
    sha="b78b777208be08edd8f297035cdfbacddb45170ad778fd643c792ee045187e39"
)
```

| Argument | Meaning | Default value |
| --- | --- | --- |
| name | The target name | Same as any other target, which is the directory name |
| repository | Fully qualified repository name | Required |
| sha | The digest of the image, minus the @sha: prefix. | Required |
| decsription | A description of the target | "" |
| tags | List of tags | [] |

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

| Argument | Meaning | Default value |
| --- | --- | --- |
| name | The target name | Same as any other target, which is the directory name |
| repository | Fully qualified repository name | Required |
| variants | Dictionary with local tags to the remote sha | Required |
| decsription | A description of the target | "" |
| tags | List of tags | [] |

### `oci_build_image`

Pull multiple shas for an image, generating a target for each.

``` python
oci_build_image(
    name="my-server",
    base=":python#slim",
    repository="my-registry.example.com/a-namespace/an-image",
    tag="latest",
    packages=[":my_pex"]
)
```

| Argument | Meaning | Default value |
| --- | --- | --- |
| name | The target name | Same as any other target, which is the directory name |
| base | The base image to use. Matches the `FROM` directive in a Dockerfile | Required |
| packages | Packaged targets to include. The first element will be used as the entrypoint. | [] |
| repository | Fully qualified repository name | Required when publishing |
| tag | Remote tag to use | Required when publishing |
| decsription | A description of the target | "" |
| tags | List of tags | [] |
