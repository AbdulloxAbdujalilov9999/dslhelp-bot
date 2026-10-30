from __future__ import annotations

import os
import tempfile

_fd, _path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_path}"
os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN-FOR-UNIT-TESTS")
os.environ.setdefault("ADMIN_IDS", "999")

import pytest_asyncio  # noqa: E402

from dslhelp import db as db_module  # noqa: E402
from fakes import FakeBot, FakeContext  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def reset_db():
    async with db_module.engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.drop_all)
        await conn.run_sync(db_module.Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
def bot():
    return FakeBot()


@pytest_asyncio.fixture
def ctx(bot):
    return FakeContext(bot)
