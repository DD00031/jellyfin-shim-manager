"""Generates join-qr.png: a QR code for the /join page composited onto
ready.png, shown on the status monitor's idle screen (see monitor.py's
IMAGE_FILENAMES["idle"]).

qrcode/Pillow are imported lazily inside generate_join_qr() rather than at
module level, so importing this module (e.g. just to build the join URL)
never requires them -- only actually generating the composite does.
"""

from pathlib import Path

QR_SIZE = 700
QR_CENTER_X = 900
QR_CENTER_Y = 960


class QrGenerationError(RuntimeError):
    """Raised when join-qr.png can't be generated (missing ready.png, etc.)."""


def join_url(cfg: dict) -> str:
    scheme = "https" if cfg.get("tls_enabled") else "http"
    return f"{scheme}://{cfg['local_ip']}:{cfg['bind_port']}/join"


def generate_join_qr(cfg: dict, force: bool = False) -> Path:
    """Composites a QR code for the join page onto a copy of ready.png.

    Skips regeneration if join-qr.png already exists and force is False --
    same "don't clobber customization" behavior as _install_default_images.
    Raises QrGenerationError if ready.png isn't there yet, or ImportError if
    qrcode/Pillow aren't installed.
    """
    import qrcode
    from PIL import Image

    image_dir = Path(cfg["image_dir"])
    base_path = image_dir / "ready.png"
    dest_path = image_dir / "join-qr.png"

    if dest_path.exists() and not force:
        return dest_path

    if not base_path.exists():
        raise QrGenerationError(
            f"{base_path} not found -- run `jellyfin-shim-manager setup` first "
            "to install the placeholder status images."
        )

    qr_img = qrcode.make(join_url(cfg)).convert("RGB").resize((QR_SIZE, QR_SIZE))

    # (900, 960) is the QR code's *center*, not its top-left corner --
    # translate to a paste origin.
    paste_x = QR_CENTER_X - QR_SIZE // 2
    paste_y = QR_CENTER_Y - QR_SIZE // 2

    base = Image.open(base_path).convert("RGB")  # read-only copy; ready.png itself is left untouched
    composite = base.copy()
    composite.paste(qr_img, (paste_x, paste_y))
    composite.save(dest_path)

    return dest_path
