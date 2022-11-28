# BitWarden backend for Pants

[![PyPI](https://img.shields.io/pypi/v/pants-backend-bitwarden?label=Latest%20release)](https://pypi.org/project/pants-backend-bitwarden)

> **Warning**
> This plugin is in development. No stability is guaranteed! Contributions welcome.

This backends implements targets for reading BitWarden secrets.

* [bw](https://bitwarden.com/help/cli/) - the BitWarden CLI client

## Planned and missing features

* Setting and creating secrets via Pants

## Targets

### `bw_item`

Matches one entry in your vault.

``` python
bw_item(
    name="pypi",
    id="386c6037-cbdd-4aa3-ba80-9ed6661f751b",
    session_secret=":bw_session_key",
)
```

| Argument         | Meaning                                                         | Default value                                         |
|------------------|-----------------------------------------------------------------|-------------------------------------------------------|
| `name`           | The target name                                                 | Same as any other target, which is the directory name |
| `id`             | Item id used by this target as seen in the address bar          | **Required**                                          |
| `item_name`      | The name in the vault. If ambiguous this will fail. Prefer IDs. | ` `                                                   |
| `session_secret` | The secret to use for the BW_SESSION variable.                  | `env["BW_SESSION"]`                                   |
| `decsription`    | A description of the target                                     | ` `                                                   |
| `tags`           | List of tags                                                    | `[]`                                                  |


### `bw_password`

The password of an item in your vault.

``` python
bw_password(
    name="pypi_password",
    item=[":pypi"],
)
```

| Argument      | Meaning                           | Default value                                         |
|---------------|-----------------------------------|-------------------------------------------------------|
| `name`        | The target name                   | Same as any other target, which is the directory name |
| `item`        | The item containing the password. | **Required**                                          |
| `decsription` | A description of the target       | ` `                                                   |
| `tags`        | List of tags                      | `[]`                                                  |

### `bw_field`

A field from an item in your vault. These are the "Custom Fields" at the bottom of an item, not to be confused with attachments.

``` python
bw_field(
    name="pypi_token",
	field_name="api_token"
    item=[":pypi"],
)
```

| Argument      | Meaning                           | Default value                                         |
|---------------|-----------------------------------|-------------------------------------------------------|
| `name`        | The target name                   | Same as any other target, which is the directory name |
| `item`        | The item containing the password. | **Required**                                          |
| `field_name`  | The item containing the password. | **Required**                                          |
| `decsription` | A description of the target       | ` `                                                   |
| `tags`        | List of tags                      | `[]`                                                  |
