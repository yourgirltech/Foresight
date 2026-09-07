"""Synthetic insurance-card image fixtures for 03 (shared by the live OCR test
and the seed script).

Every card is CLEARLY FAKE: a made-up payer ("Northwind Health"), obviously
synthetic member IDs, and a "SYNTHETIC — NOT A REAL INSURANCE CARD" watermark
across the face. No real PII, ever. Generated with Pillow so nothing binary is
committed and the values are one source of truth.
"""
from __future__ import annotations

import io

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError("tests/_cards.py needs Pillow — pip install pillow") from exc

_W, _H = 1012, 638  # ~ credit-card aspect at 300dpi-ish
_NAVY = (18, 42, 74)
_INK = (28, 32, 40)
_MUTED = (110, 120, 135)
_PAPER = (247, 249, 252)


def _font(size: int):
    for name in ("DejaVuSans.ttf", "Arial.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


# The known-good values. A test NEVER asserts an exact value for a field a
# fixture deliberately makes illegible — see ocr_live_test.py.
CARDS: dict[str, dict] = {
    "clean": {
        "member_id": "NWH-4417-208-01",
        "group_number": "GRP-778120",
        "payer_name": "Northwind Health",
        "plan_type": "PPO",
        "illegible": (),
        "absent": (),
    },
    "blurry-member-id": {
        "member_id": "NWH-9930-114-02",
        "group_number": "GRP-551907",
        "payer_name": "Northwind Health",
        "plan_type": "HMO",
        "illegible": ("member_id",),  # blurred beyond reading -> expect null, never a guess
        "absent": (),
    },
    "no-plan-type": {
        "member_id": "NWH-2261-773-00",
        "group_number": "GRP-330481",
        "payer_name": "Northwind Health",
        "plan_type": None,           # simply not printed on this card
        "illegible": (),
        "absent": ("plan_type",),
    },
    "wrong-card-back": {             # the BACK of the card — no coverage fields visible
        "member_id": None,
        "group_number": None,
        "payer_name": None,
        "plan_type": None,
        "illegible": (),
        "absent": ("member_id", "group_number", "payer_name", "plan_type"),
    },
}


def _watermark(draw: ImageDraw.ImageDraw) -> None:
    f = _font(46)
    for y in range(-_H, _H, 150):
        draw.text((40, y + _H // 2), "SYNTHETIC — NOT A REAL INSURANCE CARD",
                  font=f, fill=(0, 0, 0, 20))


def _front(spec: dict) -> Image.Image:
    img = Image.new("RGB", (_W, _H), _PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, _W, 132), fill=_NAVY)
    d.text((44, 40), spec["payer_name"] or "Northwind Health", font=_font(52), fill=(255, 255, 255))
    if spec["plan_type"]:
        d.text((_W - 190, 52), spec["plan_type"], font=_font(40), fill=(255, 255, 255))

    rows = [("MEMBER ID", spec["member_id"]), ("GROUP", spec["group_number"]),
            ("MEMBER", "TAYLOR SYNTH-SAMPLE")]
    y = 210
    for label, value in rows:
        d.text((44, y), label, font=_font(26), fill=_MUTED)
        d.text((44, y + 34), value or "", font=_font(40), fill=_INK)
        y += 118

    d.text((44, _H - 70), "RxBIN 999999   RxPCN SYNTH   Payer ID 00000 (demo)",
           font=_font(24), fill=_MUTED)
    _watermark(d)

    if "member_id" in spec["illegible"]:
        box = (40, 200, _W - 40, 322)
        region = img.crop(box).filter(ImageFilter.GaussianBlur(9))
        img.paste(region, box)
        # a smear of glare over it too
        g = ImageDraw.Draw(img)
        g.ellipse((120, 210, 640, 300), fill=(255, 255, 255))
        img = img.filter(ImageFilter.GaussianBlur(1))
    return img


def _back() -> Image.Image:
    img = Image.new("RGB", (_W, _H), _PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 90, _W, 210), fill=(20, 20, 24))  # magnetic stripe
    d.text((44, 250), "This card is for identification only.", font=_font(28), fill=_INK)
    d.text((44, 300), "Provider services: 1-800-000-0000 (demo)", font=_font(26), fill=_MUTED)
    d.text((44, 350), "Claims: PO Box 0000, Anytown  —  SEE FRONT OF CARD",
           font=_font(26), fill=_MUTED)
    _watermark(d)
    return img


def synthetic_card(kind: str) -> tuple[bytes, dict]:
    """Return (png_bytes, spec) for one fixture kind. `spec` carries the known
    values plus which fields are deliberately illegible / absent."""
    if kind not in CARDS:
        raise KeyError(f"unknown card fixture {kind!r}; have {sorted(CARDS)}")
    spec = CARDS[kind]
    img = _back() if kind == "wrong-card-back" else _front(spec)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), spec
