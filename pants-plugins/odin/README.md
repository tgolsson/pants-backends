# Pants Backend for Odin

A Pants backend for building and linting Odin language projects.

## Features

- **Toolchain Management**: Automatically download and manage the Odin compiler
- **Target Generation**: Use `odin_sources` to automatically generate targets for `.odin` files
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

Or use tailor to automatically generate targets:

```bash
pants tailor
```

Then lint your code:

```bash
pants lint src/odin::
```