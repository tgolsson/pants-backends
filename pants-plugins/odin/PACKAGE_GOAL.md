# Odin Package Goal

The Odin package goal allows you to build and package Odin applications using the `pants package` command.

## Usage

Add an `odin_package` target to your BUILD file:

```python
odin_package(
    name="my_app",
    defines=["DEBUG=true", "VERSION=1.0.0"],  # Optional build-time defines
    dependencies=[":sources"],  # Dependencies on odin_source targets
)

odin_sources(name="sources")  # Source files in the package
```

Then run:

```bash
pants package path/to:my_app
```

## Features

- **Build-time defines**: Pass defines to the Odin compiler using the `defines` field
- **Source collection**: Automatically collects all Odin source files from dependencies
- **Internal build rule**: Uses an internal `OdinBuildRequest` that can be invoked independently
- **Error handling**: Provides clear error messages for common configuration issues

## Build Process

The package goal:

1. Collects all source files from the `odin_package` target's dependencies
2. Downloads the Odin compiler if not already available
3. Runs `odin build <directory>` with any specified defines
4. Returns a `BuiltPackage` containing the compiled binary

## Internal Rules

The implementation includes two main rules:

- `build_odin_package`: Internal rule that handles the actual Odin compilation
- `package_odin_application`: Package goal rule that wraps the build rule

This design allows the build functionality to be used independently by other rules if needed.