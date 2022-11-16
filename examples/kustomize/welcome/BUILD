python_sources()

pex_binary(name="bin", entry_point="server.py:main")

kustomize(
    name="kustomize",
    sources=[
        "server.py",
        "namespace.yaml",
        "kustomization.yaml",
    ],
    dependencies=[":bin", "//examples/kustomize/welcome/base:kustomize"],
)
