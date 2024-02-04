host_kubeconfig(
    name="config",
    context="admin@husarvagen",
    namespace="default",
)

k8s_source(
    name="namespace.yaml",
    source="namespace.yaml",
)

k8s_object(
    name="ns",
    kind="...",
    template=[":namespace.yaml"],
    kubeconfig=[":config"],
)

k8s_objects(
    name="all",
    objects=[":k8s", "//examples/k8s/welcome:ns"],
)
