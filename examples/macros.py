def pants_at_least(version: str) -> bool:
    pants = __import__("pants")

    return pants.version.PANTS_SEMVER >= pants.version.Version(version)
