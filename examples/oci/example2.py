import os


def main():
    print("Launching example.pex:")
    print("\n" + "-" * 20 + "\n")
    os.execv("examples/example.pex", ["examples/example.pex"])
