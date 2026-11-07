"""Read/write JSON either to ./data (STORAGE_MODE=local) or Azure Blob (STORAGE_MODE=blob).

Paths follow the data contract, e.g. "scored/dncr/latest.json".
Agents never touch the filesystem or Blob directly — always use these functions.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from core import config


@lru_cache(maxsize=1)
def _container():
    from azure.storage.blob import BlobServiceClient

    if not config.STORAGE_CONN:
        raise RuntimeError("STORAGE_MODE=blob but STORAGE_CONN is empty")
    svc = BlobServiceClient.from_connection_string(config.STORAGE_CONN)
    return svc.get_container_client(config.STORAGE_CONTAINER)


def write_json(path: str, obj: Any) -> None:
    data = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    if config.STORAGE_MODE == "blob":
        _container().upload_blob(path, data.encode("utf-8"), overwrite=True)
    else:
        target = config.LOCAL_DATA_DIR / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding="utf-8")


_MISSING = object()


def read_json(path: str, default: Any = _MISSING) -> Any:
    """Read JSON. If the file is missing and a default is given, return the default."""
    try:
        if config.STORAGE_MODE == "blob":
            raw = _container().download_blob(path).readall().decode("utf-8")
        else:
            raw = (config.LOCAL_DATA_DIR / path).read_text(encoding="utf-8")
        return json.loads(raw)
    except Exception:
        if default is not _MISSING:
            return default
        raise


def exists(path: str) -> bool:
    if config.STORAGE_MODE == "blob":
        return _container().get_blob_client(path).exists()
    return (config.LOCAL_DATA_DIR / path).exists()
