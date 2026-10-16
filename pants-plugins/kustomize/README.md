# Kustomize backend for Pants

This release targets Pants **2.30.0**. Install it with
`pants-backend-kustomize==0.6.1` in `[GLOBAL].plugins` in `pants.toml`.

![PyPI](https://img.shields.io/pypi/v/pants-backend-kustomize?label=Latest%20release)

> **Warning**
> This plugin is in development. No stability is guaranteed! Contributions welcome.

This backends implements targets for kustomize templates.

* [kustomize](https://github.com/kubernetes-sigs/kustomize) for overlaying state ontop of raw kubernetes files

## Unreleased tool updates

The unreleased default is standalone Kustomize `v5.8.3`. Versions `v5.7.1`,
`v5.0.3`, and `v4.5.7` are also recorded for Linux/macOS on x86-64 and ARM64.
Select one with `--kustomize-tool-version=v4.5.7` or `[kustomize-tool].version`.
Standalone Kustomize versions are independent of the Kustomize embedded in
kubectl; older entries provide compatibility choices rather than a promise of
ongoing upstream maintenance.

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
