oci_pull_images(
    name="python",
    repository="docker.io/library/python",
    variants={
        "3.14.10-slim": "ec6a14df5b63e3fbb3498e3240f7f640c230f9dca67683d23265db3866697f2d",
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
    base=[":python#3.14.10-slim"],
    repository="xx",
    tag="latest",
    entrypoint="examples/oci/file/start.sh",
    layers=[":start"],
)

python_sources()
