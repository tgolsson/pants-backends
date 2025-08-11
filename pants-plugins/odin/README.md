# Pants Backend for Odin

A Pants backend for building and linting Odin language projects.

## Features

- **Toolchain Management**: Automatically download and manage the Odin compiler
- **Target Generation**: Use `odin_sources` to automatically generate targets for `.odin` files
- **Building**: Build Odin packages and binaries using the `pants package` goal
- **Running**: Execute Odin binaries using the `pants run` goal
- **Testing**: Run Odin tests using the `pants test` goal
- **Linting**: Run `odin check` on your Odin source files using the `pants lint` goal
- **Tailor Support**: Automatically detect and create `odin_sources` targets

## Installation

Add to your `pants.toml`:

```toml
[GLOBAL]
backend_packages = [
    # ... other backends
    "pants_backend_odin",
]
```

## Usage

Create an `odin_sources` target in your BUILD file:

```python
odin_sources(
    name="lib",
    sources=["**/*.odin"],
)
```

For executable binaries, create an `odin_binary` target:

```python
odin_binary(
    name="main",
    output_path="bin/main",
    defines=["DEBUG=true"],
)
```

Or use tailor to automatically generate targets:

```bash
pants tailor
```

Then you can:

```bash
# Lint your code
pants lint src/odin::

# Build a binary
pants package src/odin:main

# Run a binary
pants run src/odin:main

# Test your code  
pants test src/odin::
```