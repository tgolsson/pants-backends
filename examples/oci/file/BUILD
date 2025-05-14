oci_pull_images(
    name="python",
    repository="docker.io/library/python",
    variants=
        {
            "3.11.10-slim": "459ee2a00675825a5cf39a34d0c4a3f7cb994c76b647c88c1605ed9aca05b97e",
        },
    architecture="amd64",
    os="linux",
)

file(name="start_file", source="start.sh")

oci_layer(
    name="start",
    packages=[":start_file"],
)

oci_image_build(
    name="image",
    base=[":python#3.11.10-slim"],
    repository="xx",
    tag="latest",
    entrypoint="examples/oci/file/start.sh",
    layers=[":start"],
)

python_sources()
