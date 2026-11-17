from . import claude, codex, commandcode, dsh

_ADAPTERS = {
    "claude": claude,
    "codex": codex,
    "commandcode": commandcode,
    "dsh": dsh,
}


def get_adapter(source: str):
    try:
        return _ADAPTERS[source]
    except KeyError as error:
        raise ValueError(f"unknown source: {source}") from error


def sources():
    return sorted(_ADAPTERS)
