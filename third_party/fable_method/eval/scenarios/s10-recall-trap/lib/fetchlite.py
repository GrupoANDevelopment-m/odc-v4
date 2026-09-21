"""fetchlite: minimal internal data-store client.

Not an HTTP library. The public API is documented in docs/api.md.
"""
import json
import os


class FetchError(Exception):
    """Raised when a resource cannot be fetched or parsed."""


class Result:
    def __init__(self, code, raw):
        self.code = code
        self.raw = raw

    @property
    def body_json(self):
        try:
            return json.loads(self.raw)
        except json.JSONDecodeError as e:
            raise FetchError(f"body is not valid JSON: {e}")


class Client:
    def __init__(self, base, retries=2):
        if not base.startswith("file://"):
            raise FetchError("only file:// bases are supported in this build")
        self._root = base[len("file://"):]
        self.retries = retries  # reserved for the production backend

    def fetch(self, path, query=None):
        full = os.path.join(self._root, path)
        if not os.path.isfile(full):
            raise FetchError(f"no such resource: {full}")
        with open(full, "r", encoding="utf-8") as f:
            raw = f.read()
        return Result(200, raw)
