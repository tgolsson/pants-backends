k8s_source(
    name="namespace.yaml",
    source="namespace.yaml",
)

k8s_object(
    name="ns",
    kind="...",
    template=[":namespace.yaml"],
    cluster="default",
    namespace="default",
)

k8s_objects(
    name="all",
    objects=[":k8s", "//examples/welcome:ns"],
)
