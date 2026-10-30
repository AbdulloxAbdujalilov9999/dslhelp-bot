"""A minimal fake Telegram API: fake Bot + Update/Message/CallbackQuery objects.

Lets Phase-1 handlers be exercised without any network call, per the project's
own "soxta Telegram API bilan sinab ko'r" testing requirement.
"""
from __future__ import annotations

import itertools

_id_counter = itertools.count(1000)


class SentMessage:
    def __init__(self, chat_id, text=None, photo=None, caption=None, reply_markup=None, document=None, filename=None):
        self.chat_id = chat_id
        self.text = text
        self.photo = photo
        self.caption = caption
        self.reply_markup = reply_markup
        self.document = document
        self.filename = filename
        self.message_id = next(_id_counter)


class FakeBot:
    def __init__(self):
        self.sent: list[SentMessage] = []
        self.sent_by_id: dict[int, SentMessage] = {}
        self.edited: list[dict] = []

    async def send_message(self, chat_id, text, reply_markup=None, reply_to_message_id=None, **kwargs):
        msg = SentMessage(chat_id, text=text, reply_markup=reply_markup)
        self.sent.append(msg)
        self.sent_by_id[msg.message_id] = msg
        return msg

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **kwargs):
        msg = SentMessage(chat_id, photo=photo, caption=caption, reply_markup=reply_markup)
        self.sent.append(msg)
        self.sent_by_id[msg.message_id] = msg
        return msg

    async def send_document(self, chat_id, document, filename=None, caption=None, **kwargs):
        data = document.read() if hasattr(document, "read") else document
        msg = SentMessage(chat_id, document=data, filename=filename, caption=caption)
        self.sent.append(msg)
        self.sent_by_id[msg.message_id] = msg
        return msg

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None, **kwargs):
        original = self.sent_by_id.get(message_id)
        if original is not None and original.photo is not None:
            raise RuntimeError("cannot edit_message_text on a photo message")
        self.edited.append({"chat_id": chat_id, "message_id": message_id, "text": text})
        if original is not None:
            original.text = text

    async def edit_message_caption(self, chat_id, message_id, caption, reply_markup=None, **kwargs):
        original = self.sent_by_id.get(message_id)
        if original is not None and original.photo is None:
            raise RuntimeError("cannot edit_message_caption on a text message")
        self.edited.append({"chat_id": chat_id, "message_id": message_id, "caption": caption})
        if original is not None:
            original.caption = caption

    def messages_to(self, chat_id) -> list[SentMessage]:
        return [m for m in self.sent if m.chat_id == chat_id]


class FakeUser:
    def __init__(self, user_id, first_name="Test", last_name=""):
        self.id = user_id
        self.first_name = first_name
        self.last_name = last_name
        self.full_name = f"{first_name} {last_name}".strip()


class FakeChat:
    def __init__(self, chat_id, title=""):
        self.id = chat_id
        self.title = title


class FakePhotoSize:
    def __init__(self, file_id):
        self.file_id = file_id


class FakeLocation:
    def __init__(self, latitude, longitude):
        self.latitude = latitude
        self.longitude = longitude


class FakeDocument:
    def __init__(self, file_id):
        self.file_id = file_id


class FakeMessage:
    def __init__(
        self,
        chat,
        user,
        text=None,
        caption=None,
        photo=None,
        reply_to_message=None,
        location=None,
        document=None,
    ):
        self.chat = chat
        self.from_user = user
        self.text = text
        self.caption = caption
        self.photo = [FakePhotoSize(photo)] if photo else []
        self.reply_to_message = reply_to_message
        self.location = FakeLocation(*location) if location else None
        self.document = FakeDocument(document) if document else None
        self.message_id = next(_id_counter)
        self.replies: list[SentMessage] = []

    async def reply_text(self, text, reply_markup=None, **kwargs):
        msg = SentMessage(self.chat.id, text=text, reply_markup=reply_markup)
        self.replies.append(msg)
        return msg


class FakeCallbackQuery:
    def __init__(self, data, user, message, chat):
        self.data = data
        self.from_user = user
        self.message = message
        self.chat = chat
        self.answers: list[dict] = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append({"text": text, "show_alert": show_alert})

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        self.message.text = text
        return self.message


class FakeUpdate:
    def __init__(self, message=None, callback_query=None):
        self.message = message
        self.callback_query = callback_query

    @property
    def effective_chat(self):
        if self.callback_query:
            return self.callback_query.chat
        return self.message.chat

    @property
    def effective_user(self):
        if self.callback_query:
            return self.callback_query.from_user
        return self.message.from_user

    @property
    def effective_message(self):
        if self.callback_query:
            return self.callback_query.message
        return self.message


class FakeContext:
    def __init__(self, bot, args=None):
        self.bot = bot
        self.args = args or []


def make_command_update(chat, user, text, photo=None, caption=None, reply_to=None, location=None, document=None):
    """Builds a fake Update for a plain message, photo-with-caption, location, or document.

    Command arguments are NOT parsed from `text` here (that's PTB's job in production);
    set `ctx.args` explicitly before calling a handler that reads context.args.
    `location` is an (lat, lon) tuple; `document` is a file_id string.
    """
    msg = FakeMessage(
        chat, user, text=text, caption=caption, photo=photo, reply_to_message=reply_to,
        location=location, document=document,
    )
    return FakeUpdate(message=msg)


def make_callback_update(chat, user, data, message=None):
    msg = message or FakeMessage(chat, user)
    query = FakeCallbackQuery(data, user, msg, chat)
    return FakeUpdate(callback_query=query)
