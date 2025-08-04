# Odin Backend Usage Examples

This directory contains examples of how to use the Odin backend for Pants.

## Installation

Add to your `pants.toml`:

```toml
[GLOBAL]
pythonpath.add = ["path/to/pants-plugins/odin"]
backend_packages.add = ["pants_backend_odin"]
```

## Example Project Structure

```
my_odin_project/
├── BUILD
├── main.odin
└── lib.odin
```

## BUILD File

```python
# Auto-generated with pants tailor, or manually created:
odin_sources(
    name="my_odin_code",
    sources=["*.odin"],
)
```

## Usage

```bash
# Auto-generate BUILD files for Odin sources
pants tailor

# Lint Odin code
pants lint src/odin::

# List all Odin targets  
pants list --filter-target-type=odin_sources ::
```

## Configuration

The Odin backend can be configured in `pants.toml`:

```toml
[odin-tool]
# Skip linting (default: false)
skip = false

# Enable tailor support (default: true)
tailor = true

# Specify version (default: v0.13.0)
version = "v0.13.0"
```