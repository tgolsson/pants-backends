import os
import platform
import socket
import sys


def main():
    print("Hello from inside Runc!")
    print("Args: ", sys.argv)
    print("Python Version:", sys.version)

    print("Hostname:", socket.gethostname())
    my_system = platform.uname()

    print(f"System: {my_system.system}")
    print(f"Release: {my_system.release}")
    print(f"Version: {my_system.version}")
    print(f"Machine: {my_system.machine}")
    print(f"Processor: {my_system.processor}")

    print("Env:")
    for k, v in os.environ.items():
        print("\t", k, v)
