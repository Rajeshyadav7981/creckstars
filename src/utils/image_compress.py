"""Target-size image compression for user uploads.

The client compresses before sending, but it can still arrive at multi-MB
sizes (large cameras, low compression on older devices, raw photos picked
from the gallery). We land bytes on disk, so this is where we enforce a
storage budget: re-encode to JPEG, step quality down, and downscale as a
last resort until we fit under ``target_bytes``.
"""
import io

from PIL import Image as PILImage

# 2 MB is the default cap for on-disk size. Rationale: well under the free
# tier for most VMs, comfortable for mobile download, and a photo that
# can't be compressed into 2 MB at reasonable quality is almost certainly
# being uploaded at far higher resolution than the UI renders anyway.
DEFAULT_TARGET_BYTES = 2 * 1024 * 1024

# Quality ladder: each step shaves ~20-40% off. We stop at the first value
# that fits the target.
_QUALITY_LADDER = (85, 75, 65, 55, 45, 35, 25)

# Minimum width before we give up and return whatever we have. Shrinking
# past this loses too much fidelity to be worth storing.
_MIN_WIDTH = 400


def compress_to_target_size(
    img: PILImage.Image,
    target_bytes: int = DEFAULT_TARGET_BYTES,
    max_width: int = 1600,
) -> bytes:
    """Return JPEG bytes for ``img`` sized ≤ ``target_bytes`` when possible.

    Steps:
      1. Resize down to ``max_width`` if wider (preserves aspect ratio).
      2. Re-encode JPEG at a descending quality ladder; return the first fit.
      3. If the lowest ladder quality still overruns, iteratively shrink
         dimensions by 20% at quality=60 until we fit or hit ``_MIN_WIDTH``.
      4. Return the smallest we produced even if target wasn't reached —
         callers get "best effort" without an exception when the source is
         pathological (e.g. a 50MP panorama).
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)), PILImage.LANCZOS)

    best_data: bytes | None = None

    for quality in _QUALITY_LADDER:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if best_data is None or len(data) < len(best_data):
            best_data = data
        if len(data) <= target_bytes:
            return data

    # Still too big at the lowest ladder quality — shrink dimensions.
    current = img
    while current.width > _MIN_WIDTH:
        new_w = int(current.width * 0.8)
        new_h = max(1, int(current.height * 0.8))
        current = current.resize((new_w, new_h), PILImage.LANCZOS)
        buf = io.BytesIO()
        current.save(buf, format="JPEG", quality=60, optimize=True)
        data = buf.getvalue()
        if len(data) < len(best_data):
            best_data = data
        if len(data) <= target_bytes:
            return data

    return best_data  # type: ignore[return-value]
