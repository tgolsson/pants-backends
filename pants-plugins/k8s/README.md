:warning: This plugin is in development. No stability is guaranteed! Contributions welcome.

# Kubernetes backend for Pants

This backends implements targets for working with Kubernetes clusters using raw YAML.

* [kubectl](https://github.com/kubernetes/kubectl) for cluster operations

## Targets

There's currently three targets for `pants-backend-k8s`:

* [`k8s_source`](#k8s_source)
* [`k8s_object`](#k8s_object)
* [`k8s_objects`](#k8s_objects)

### `k8s_source`

A file that can be passed into other k8s fields that are not source fields. For example, `k8s_object.template`.

``` python
k8s_source(
    name="namespace.yaml",
    source="namespace.yaml",
)
```


| Argument | Meaning | Default value |
| --- | --- | --- |
| name | The target name | Same as any other target, which is the directory name |
| source | The raw file | Required |
| decsription | A description of the target | "" |
| tags | List of tags | [] |


This'll eventually be automated like other rules once a suitable heuristic for generation with tailor is found. PRs welcome!


### `k8s_object`

Input for a kubernetes command, either generated via [`kustomize`](https://github.com/tgolsson/pants-backend-kustomize#kustomize) or via [`k8s_source`](#k8s_source).

``` python
k8s_object(
    name="k8s",
    description="the chat backend"
    template=[":kustomize"],
    namespace="chat-app",
    cluster="prod",
)
```


| Argument | Meaning | Default value |
| --- | --- | --- |
| name | The target name | Same as any other target, which is the directory name |
| template | The target to act on | Required |
| namespace | Namespace to target | Optional, will use default kubectl namespace |
| cluster | cluster to target | Optional, will use default kubectl cluster |
| decsription | A description of the target | "" |
| tags | List of tags | [] |

`k8s_object` is a generator for `kubernetes` target parametrized by the potential commands that are available: `apply`,
`create`, `get`, `describe`, `replace`, and `delete`.

### `k8s_objects`

A collection of kubernetes objects that should be managed together.

``` python
k8s_objects(
    name="my-service",
    description="all components of service-x"
    objects=[":namespace", ":deployment"],
)
```


| Argument | Meaning | Default value |
| --- | --- | --- |
| name | The target name | Same as any other target, which is the directory name |
| objects | k8s_object targets that should be managed | Required |
| decsription | A description of the target | "" |
| tags | List of tags | [] |

Like `k8s_object`, `k8s_objects` is a generator for parametrized targets for the commands that are available: `apply`,
`create`, `get`, `describe`, `replace`, and `delete`.
