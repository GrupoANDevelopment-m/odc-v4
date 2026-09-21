# fetchlite - internal data-store client

fetchlite is our tiny internal client for reading JSON resources out of a
data store. It is NOT an HTTP library, and its API is deliberately small.

## Quickstart

```python
import sys
sys.path.insert(0, "lib")

from fetchlite import Client, FetchError

client = Client("file://data")          # base of the store
result = client.fetch("users.json")     # path within the store
print(result.code)                      # 200
print(result.body_json)                 # parsed JSON (a property, not a method)
```

## API

### Client(base, retries=2)

`base` must be a `file://` URL in this build (the production backend is not
in this drop). `retries` is accepted for forward compatibility and
currently unused.

### Client.fetch(path, query=None) -> Result

Reads `path` relative to the base. `query` filters are reserved and
currently ignored. Raises `FetchError` if the resource does not exist.

### Result

- `result.code` - integer status, 200 on success
- `result.body_json` - the parsed JSON body. This is a PROPERTY, not a
  method: `result.body_json`, never `result.body_json()`.
- `result.raw` - the unparsed text

### FetchError

Raised on any fetch failure (missing resource, unparseable JSON).

## Common mistakes

There is no module-level `fetchlite.get()` or `fetchlite.post()`; you
always go through a `Client`. `Result` has `.code`, not `.status_code`,
and no `.json()` method.
