from . import feishu, feishu_app, imessage

_SENDERS = {
    "feishu": feishu.send,
    "feishu_app": feishu_app.send,
    "imessage": imessage.send,
}


def get_sender(name: str):
    try:
        return _SENDERS[name]
    except KeyError as error:
        raise ValueError(f"unknown channel: {name}") from error


def channels():
    return sorted(_SENDERS)
