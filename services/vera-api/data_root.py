import os

from data_paths import STORE_PATHS


def resolve() -> str:
    explicit = os.environ.get("VERA_DATA_DIR", "").strip()
    if explicit:
        root = os.path.expanduser(explicit)
        os.makedirs(root, exist_ok=True)
        return root
    if os.path.isdir("/data") and os.access("/data", os.W_OK):
        return "/data"
    root = os.path.join(os.path.expanduser("~"), ".vera", "data")
    os.makedirs(root, exist_ok=True)
    return root


def apply() -> str:
    root = resolve()
    for var, rel in STORE_PATHS.items():
        os.environ.setdefault(var, os.path.join(root, rel))
    return root
