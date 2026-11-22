"""Uvicorn loop factory; PG's official psycopg saver requires Selector on Windows."""

import asyncio
import os

from app.config import settings


def new_loop() -> asyncio.AbstractEventLoop:
    if os.name == "nt" and settings.db_backend == "postgresql":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()
