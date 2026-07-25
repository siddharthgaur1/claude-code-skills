"""A perfectly ordinary, small source file.

The auditor should NOT flag this: it is well under the size threshold and
matches none of the bloat patterns. Its presence in the fixture proves the
tool does not cry wolf on normal code.
"""


def greet(name: str) -> str:
    return f"hello, {name}"


if __name__ == "__main__":
    print(greet("world"))
