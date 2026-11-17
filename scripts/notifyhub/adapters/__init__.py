from . import codex, dsh

_ADAPTERS = {"codex": codex, "dsh": dsh}


def get_adapter(source: str):
    try:
        return _ADAPTERS[source]
    except KeyError as error:
        raise ValueError(f"unknown source: {source}") from error


def sources():
    return sorted(_ADAPTERS)
