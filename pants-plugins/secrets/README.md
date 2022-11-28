# Secrets backend for Pants

[![PyPI](https://img.shields.io/pypi/v/pants-backend-secrets?label=Latest%20release)](https://pypi.org/project/pants-backend-secrets)

> **Warning**
> This plugin is in development. No stability is guaranteed! Contributions welcome.

This backends implements utilities for handling secrets.

## Planned and missing features

* Setting and creating secrets via Pants

## Targets

### `env_secret`

A secret to be read from the environment.

``` python
env_secret(
    name="bw_session_key",
    key="BW_SESSION",
)
```

| Argument      | Meaning                           | Default value                                         |
|---------------|-----------------------------------|-------------------------------------------------------|
| `name`        | The target name                   | Same as any other target, which is the directory name |
| `key`         | The environment variable to read. | **Required**                                          |
| `decsription` | A description of the target       | ` `                                                   |
| `tags`        | List of tags                      | `[]`                                                  |


## Goals

### `decrypt`

Decrypts and prints a secret.

```console
BW_SESSION="..." pants decrypt //examples/bitwarden:pypi_token
22:29:42.17 [INFO] Completed: Decrypting examples/bitwarden:pypi_token
Secret examples/bitwarden:pypi_token from BitWarden: pypi-...
```
