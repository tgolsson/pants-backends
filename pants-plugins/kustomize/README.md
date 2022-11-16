:warning: This plugin is in development. No stability is guaranteed! Contributions welcome.

# Kustomize backend for Pants

This backends implements targets for kustomize templates.

* [kustomize](https://github.com/kubernetes-sigs/kustomize) for overlaying state ontop of raw kubernetes files

## Planned and missing features

* Injecting more state into the templates, specifically image SHAs from the built-in Pants backend for docker, or from
  [pants-backend-oci](https://github.com/tgolsson/pants-backend-oci).
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
| name | The target name | Same as any other target, which is the directory name |
| sources | Resources used by this target | Required |
| dependencies | Targets to package and pass to the build context, as well as bases | [] |
| decsription | A description of the target | "" |
| tags | List of tags | [] |

For dependencies, the builder will replace labels in the kustomization.yaml with the path of the built package.
