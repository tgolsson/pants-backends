# Kustomize backend for Pants

![PyPI](https://img.shields.io/pypi/v/pants-backend-kustomize?label=Latest%20release)

> **Warning**
> This plugin is in development. No stability is guaranteed! Contributions welcome.

This backends implements targets for kustomize templates.

* [kustomize](https://github.com/kubernetes-sigs/kustomize) for overlaying state ontop of raw kubernetes files

## Planned and missing features

* Key/secret/... generation from built artifacts

## Targets

There's currently one target.


### `kustomize`

A code-generation target for converting a bundle of kubernetes files into a single multi-docuent YAML file with state
injected from other Pants targets.


``` python
kustomize(
    name="kustomize",
    sources=[
        "deployment.yaml",
        "server.py",
        "service.yaml",
        "namespace.yaml",
		"kustomization.yaml",
    ],
    dependencies=[":bin"],
)
```

| Argument | Meaning | Default value |
| --- | --- | --- |
| `name` | The target name | Same as any other target, which is the directory name |
| `sources` | Resources used by this target | **Required** |
| `dependencies` | Targets to package and pass to the build context, as well as bases | `[]` |
| `decsription` | A description of the target | ` ` |
| `tags` | List of tags | `[]` |

For dependencies, the builder will replace labels in the kustomization.yaml with the path of the built package.
