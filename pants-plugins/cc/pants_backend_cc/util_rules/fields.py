"""Reading the `sources` and `dependencies` fields of C/C++ targets the same way in every
backend."""

from __future__ import annotations

from pants.engine.addresses import Address, AddressInput
from pants.engine.internals.build_files import maybe_resolve_address, resolve_address


async def parse_address(value: str, relative_to: str, description_of_origin: str) -> Address:
    """One address string of a BUILD field, resolved as Pants does (relative to the BUILD
    file's directory)."""
    address_input = AddressInput.parse(
        value, relative_to=relative_to, description_of_origin=description_of_origin
    )
    maybe = await maybe_resolve_address(address_input)
    return await resolve_address(maybe)


def overlap_error(address: Address, sources: tuple[str, ...], extra: tuple[str, ...]) -> str | None:
    """Two copies of one path must never reach a compile: which one won would depend on merge
    order."""
    overlap = sorted(set(sources) & set(extra))
    if not overlap:
        return None
    first = overlap[0]
    prefix = f"{address.spec_path}/" if address.spec_path else ""
    relative = first[len(prefix) :] if first.startswith(prefix) else first
    return (
        f"{address}: `sources` and a dependency both provide {overlap}; drop it from one of them, "
        f'e.g. "!{relative}" in `sources`'
    )
