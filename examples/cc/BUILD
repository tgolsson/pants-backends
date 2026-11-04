# Platform names are global: these are the ones every example builds for.
platform(name="linux-x86_64", os="linux", arch="x86_64")
platform(name="macos-aarch64", os="macos", arch="aarch64")

# The host toolchains. A pair that isn't this machine's is left unused.
cc_toolchain(
    name="linux",
    host="linux-x86_64",
    target="linux-x86_64",
    cc="gcc",
    cxx="g++",
    ar="ar",
)
cc_toolchain(
    name="macos",
    host="macos-aarch64",
    target="macos-aarch64",
    cc="clang",
    cxx="clang++",
    ar="ar",
)

# An executable linked against the CMake project's static library.
cc_binary(
    name="greet",
    sources=["app/main.c"],
    dependencies=["examples/cc/cmake#greet"],
    output_path="examples/cc/greet",
)

# `pants test` builds and runs it against the same library.
cc_test(
    name="greet_test",
    sources=["test/greet_test.c"],
    dependencies=["examples/cc/cmake#greet"],
)
