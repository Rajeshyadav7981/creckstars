"""Image storage — local VM disk.

Centralises image persistence so the upload endpoints stay DRY. Writes to
backend/uploads/<subdir>/<filename> and returns the relative '/uploads/...' URL
that the StaticFiles mount serves.
"""
import os
import asyncio

from src.app.api.config import UPLOADS_DIR


def _blocking_write(path: str, content: bytes) -> None:
    with open(path, "wb") as f:
        f.write(content)


async def save_image(content: bytes, subdir: str, filename: str,
                     content_type: str = "image/jpeg") -> str:
    d = os.path.join(UPLOADS_DIR, subdir)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, filename)
    if not os.path.realpath(path).startswith(os.path.realpath(d) + os.sep):
        raise ValueError("Invalid upload path")
    await asyncio.to_thread(_blocking_write, path, content)
    return f"/uploads/{subdir}/{filename}"
