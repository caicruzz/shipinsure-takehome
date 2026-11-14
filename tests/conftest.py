from __future__ import annotations

import pytest

from chalicelib.db import SessionFactory
from tests.helpers import fresh_db


@pytest.fixture
def session_factory() -> SessionFactory:
    return fresh_db()
