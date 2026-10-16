# MDBook backend for Pants

This release targets Pants **2.30.0**. Install it with
`pants-backend-mdbook==0.7.0` in `[GLOBAL].plugins` in `pants.toml`.

![PyPI](https://img.shields.io/pypi/v/pants-backend-mdbook?label=Latest%20release)

> **Warning**
> This plugin is in development. No stability is guaranteed! Contributions welcome.

This provides a tool for building mdbook targets with pants. There is currently a single very simple rule:

``` python
md_book(
    name="my-docs",
    sources=["book.toml", "src/*"],
)
```

| Argument | Meaning | Default value |
| --- | --- | --- |
| `name` | The target name | Same as any other target, which is the directory name |
| `sources` | Files included when building the book | `book.toml` and the `src` directory |
| `decsription` | A description of the target | `""` |
| `tags` | List of tags | `[]` |
## Unreleased tool updates

The unreleased default is mdBook `v0.5.4`. The latest 0.4 release, `v0.4.52`,
and existing historical versions remain selectable via `--mdbook-version=v0.4.52`
or `[mdbook].version`. The new versions include native Linux/macOS binaries for
x86-64 and ARM64; retained older macOS ARM64 entries still use Rosetta.

When upgrading a book to mdBook 0.5, remove `[book].multilingual` from `book.toml`:
that obsolete setting is now rejected. Select 0.4.52 if a book or preprocessor
still requires the 0.4 series.
