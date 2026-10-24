# Scripted build steps: an escape hatch that runs commands in the image with `podman build`.
# String commands run through a shell, so these use Debian rather than distroless; a list of strings
# runs without one (see examples/oci/multistage).

file(name="greeting_file", source="greeting.txt")

# Runs a command in the image and keeps only `outputs`, as a layer for other images.
oci_build_layer(
    name="greeting-layer",
    base=["//examples/oci/debian:debian#bookworm-20241016-slim"],
    packages=[":greeting_file"],
    commands=(
        "mkdir -p /opt/greeting && tr a-z A-Z < /examples/oci/scripted/greeting.txt > /opt/greeting/loud.txt"
    ),
    outputs=["opt/greeting"],
)

# Copies files out of an image without running anything.
oci_extract(
    name="os-release",
    base=["//examples/oci/debian:debian#bookworm-20241016-slim"],
    outputs=["usr/lib/os-release"],
)

# Runs a command on top of the image's layers; its changes become one more layer.
oci_image_build(
    name="image",
    base=["//examples/oci/debian:debian#bookworm-20241016-slim"],
    packages=[":greeting-layer"],
    commands="rm -rf /var/lib/apt/lists && cp /opt/greeting/loud.txt /etc/motd",
    repository="pants-backend-oci/examples/scripted",
    tag="latest",
    entrypoint="/bin/cat",
    args=["/etc/motd"],
)
