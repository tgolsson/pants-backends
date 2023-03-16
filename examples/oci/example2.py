import os


def main():
    print("Launching example.pex:")
    print("\n" + "-" * 20 + "\n", flush=True)
    os.execv("examples.oci/example.pex", ["examples.oci/example.pex"])
