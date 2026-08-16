from collections.abc import Iterator
from pathlib import Path

import pytest

from dbagent.db.database import Database

CHINOOK_PATH = Path(__file__).resolve().parent.parent / "data" / "chinook.sqlite"


@pytest.fixture
def chinook() -> Iterator[Database]:
    db = Database(CHINOOK_PATH)
    yield db
    db.close()
