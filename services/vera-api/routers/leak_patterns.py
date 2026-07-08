import os
import re

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leak_patterns.txt")


def _load() -> list[tuple[str, str]]:
    out = []
    with open(_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            name, _, shape = line.partition("\t")
            if name and shape:
                out.append((name, shape))
    return out


KEY_SHAPES = _load()

_COMPILED = [re.compile(shape) for _, shape in KEY_SHAPES]


def looks_secret(value: str) -> bool:
    return any(rx.search(value) for rx in _COMPILED)
