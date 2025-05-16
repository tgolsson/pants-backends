# Mirroring backend

Utility backend for mirroring images from one registry to another, or internally in one registry.

Activate with:

```toml
backend_packages = [
    ...,
    "pants_backend_oci.utility.mirror",
]
```

## Targets

### `oci_mirror_image`

Mirror a single image.

``` python
oci_mirror_image(
    name="base-python",
    repository="docker.io/library/python",
    sha="b78b777208be08edd8f297035cdfbacddb45170ad778fd643c792ee045187e39"
)
```

| Argument                 | Meaning                                          | Default value      |
|--------------------------|--------------------------------------------------|--------------------|
| `name`                   | The target name                                  | The directory name |
| `source_repository`      | Fully qualified repository name to pull from     | **Required**       |
| `destination_repository` | Fully qualified repository name to pull from     | **Required**       |
| `sha`                    | The digest of the image, minus the @sha: prefix. | **Required**       |
| `tag`                    | Optional tag to add when pushing.                |                    |
| `decsription`            | A description of the target                      |                    |
| `tags`                   | List of tags                                     | `[]`               |


### `oci_mirror_images`

Mirror multiple shas for one image.

``` python
oci_mirror_images(
    name="base-python",
    repository="docker.io/library/python",
	variants={
	    "some-variant": "b78b777208be08edd8f297035cdfbacddb45170ad778fd643c792ee045187e39",
	    "other-tag": "b78b777208be08edd8f297035cdfbacddb45170ad778fd643c792ee045187e39",
    },
)
```

| Argument                 | Meaning                                          | Default value      |
|--------------------------|--------------------------------------------------|--------------------|
| `name`                   | The target name                                  | The directory name |
| `source_repository`      | Fully qualified repository name to pull from     | **Required**       |
| `destination_repository` | Fully qualified repository name to pull from     | **Required**       |
| `variants` | Dictionary with tags-to-sha. | **Required** |
| `decsription`            | A description of the target                      |                    |
| `tags`                   | List of tags                                     | `[]`               |
