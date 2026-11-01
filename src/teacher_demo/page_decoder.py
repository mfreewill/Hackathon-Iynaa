"""Socket-free bounded page decoder: PNG/JPEG plus selected PDF page.

Decodes one uploaded PNG/JPEG page, or renders one selected page of
an uploaded PDF at 216 DPI, to PNG bytes plus a
``DecodedSourcePage(SourcePage(...))``. No crop/resize/OCR/model
action is performed. Every PDFium operation is serialized under one
process-wide lock.
"""
from __future__ import annotations

import hashlib
import io
import math
import threading
import uuid
from dataclasses import dataclass

import PIL
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
from PIL import Image, ImageOps
from pypdfium2.version import PDFIUM_INFO, PYPDFIUM_INFO
from source_contract import SourcePage

from .adapter import DecodedSourcePage

MAX_ORIGINAL_BYTES = 20 * 1024 * 1024
MAX_EDGE_PIXELS = 12_000
MAX_PIXELS = 20_000_000
PDF_RENDER_DPI = 216

_MEDIA_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg"}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8"
_PNG_IEND_CHUNK = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
_PDF_SIGNATURE = b"%PDF-"
_PDF_MEDIA_TYPE = "application/pdf"
_PDF_SCALE = PDF_RENDER_DPI / 72
_PDF_DECODER = f"pypdfium2 {PYPDFIUM_INFO} / PDFium {PDFIUM_INFO}"
_PDFIUM_LOCK = threading.Lock()


class PageDecodeError(Exception):
    """Typed, value-free refusal to decode an uploaded page."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DecodedPage:
    original_bytes: bytes
    png_bytes: bytes
    decoded_source: DecodedSourcePage
    media_type: str
    page_count: int
    decoder: str
    render_dpi: object = None
    exif_orientation: object = None
    orientation_applied: bool = False


def _refuse(code, message):
    raise PageDecodeError(message, code)


def _looks_like_supported(data):
    return data.startswith(_PNG_SIGNATURE) or data.startswith(_JPEG_SIGNATURE)


def _validate_dimensions(width, height):
    for value in (width, height):
        if (isinstance(value, bool)
                or not isinstance(value, (int, float))
                or (isinstance(value, float)
                    and (not math.isfinite(value)
                         or not value.is_integer()))
                or value <= 0):
            _refuse("invalid_dimensions",
                    "page decoding refused: invalid image dimensions")
    width, height = int(width), int(height)
    if max(width, height) > MAX_EDGE_PIXELS or width * height > MAX_PIXELS:
        _refuse("image_too_large",
                "page decoding refused: image dimensions exceed limits")
    return width, height


def _exif_orientation(image):
    try:
        exif = image.getexif()
        if not exif:
            return None
        value = exif.get(274)
    except Exception:
        raise PageDecodeError(
            "page decoding refused: malformed image",
            "malformed_image") from None
    return value if type(value) is int else None


def _open_image(data):
    buffer = io.BytesIO(data)
    try:
        image = Image.open(buffer)
    except Image.DecompressionBombError:
        buffer.close()
        raise PageDecodeError(
            "page decoding refused: image dimensions exceed limits",
            "image_too_large") from None
    except Exception:
        buffer.close()
        if _looks_like_supported(data):
            raise PageDecodeError(
                "page decoding refused: malformed image",
                "malformed_image") from None
        raise PageDecodeError(
            "page decoding refused: unsupported format",
            "unsupported_format") from None
    return buffer, image


def _checked_header(image):
    fmt = image.format
    if fmt not in _MEDIA_TYPES:
        _refuse("unsupported_format",
                "page decoding refused: unsupported format")
    media_type = _MEDIA_TYPES[fmt]
    try:
        raw_width, raw_height = image.size
    except Exception:
        raise PageDecodeError(
            "page decoding refused: malformed image",
            "malformed_image") from None
    # Dimension bounds precede frame/metadata inspection and all loads.
    _validate_dimensions(raw_width, raw_height)
    try:
        n_frames = getattr(image, "n_frames", 1)
        is_animated = getattr(image, "is_animated", False)
    except Exception:
        raise PageDecodeError(
            "page decoding refused: malformed image",
            "malformed_image") from None
    if is_animated or (type(n_frames) is int and n_frames != 1):
        _refuse("multiframe_image",
                "page decoding refused: multiframe image")
    if type(n_frames) is not int:
        _refuse("malformed_image",
                "page decoding refused: malformed image")
    return fmt, media_type


def _verify_png_container(image, data):
    # Pillow's PNG verifier checks the chunks it visits but stops at
    # the IEND header; also require the actual first IEND (found by
    # walking chunk headers) to be the complete empty terminal chunk
    # with its CRC ending exactly at end of input.
    if not data.startswith(_PNG_SIGNATURE):
        _refuse("malformed_image",
                "page decoding refused: malformed image")
    pos = len(_PNG_SIGNATURE)
    end = len(data)
    while True:
        if pos + 8 > end:
            _refuse("malformed_image",
                    "page decoding refused: malformed image")
        length = int.from_bytes(data[pos:pos + 4], "big")
        if data[pos + 4:pos + 8] == b"IEND":
            if (length != 0 or pos + 12 != end
                    or data[pos:pos + 12] != _PNG_IEND_CHUNK):
                _refuse("malformed_image",
                        "page decoding refused: malformed image")
            break
        if length > end - pos - 12:
            _refuse("malformed_image",
                    "page decoding refused: malformed image")
        pos += 12 + length
    try:
        image.verify()
    except Image.DecompressionBombError:
        raise PageDecodeError(
            "page decoding refused: image dimensions exceed limits",
            "image_too_large") from None
    except Exception:
        raise PageDecodeError(
            "page decoding refused: malformed image",
            "malformed_image") from None


def _decode_and_render(image):
    try:
        image.load()
    except Image.DecompressionBombError:
        raise PageDecodeError(
            "page decoding refused: image dimensions exceed limits",
            "image_too_large") from None
    except Exception:
        raise PageDecodeError(
            "page decoding refused: malformed image",
            "malformed_image") from None
    # Only after a successful pixel load: for PNG, getexif() can
    # itself trigger load(), and a failed load must never be
    # treated as merely missing EXIF.
    exif_orientation = _exif_orientation(image)
    orientation_applied = exif_orientation in (2, 3, 4, 5, 6, 7, 8)
    try:
        transposed = ImageOps.exif_transpose(image)
    except Exception:
        raise PageDecodeError(
            "page decoding refused: malformed image",
            "malformed_image") from None
    if transposed is None:
        _refuse("malformed_image",
                "page decoding refused: malformed image")
    try:
        if isinstance(transposed.info, dict):
            # Pixels are already oriented; do not let the returned
            # PNG re-apply an orientation downstream. Other info
            # (for example palette transparency) is retained.
            transposed.info.pop("exif", None)
        width, height = _validate_dimensions(*transposed.size)
        converted = None
        to_save = transposed
        if transposed.mode in ("CMYK", "YCbCr"):
            try:
                converted = transposed.convert("RGB")
            except Exception:
                raise PageDecodeError(
                    "page decoding refused: malformed image",
                    "malformed_image") from None
            to_save = converted
        out = io.BytesIO()
        try:
            try:
                to_save.save(out, format="PNG")
            except Exception:
                raise PageDecodeError(
                    "page decoding refused: malformed image",
                    "malformed_image") from None
            png = out.getvalue()
        finally:
            out.close()
            if converted is not None:
                converted.close()
    finally:
        if transposed is not image:
            transposed.close()
    return png, width, height, exif_orientation, orientation_applied


def _malformed_pdf():
    return PageDecodeError("page decoding refused: malformed PDF",
                           "malformed_pdf")


def _encrypted_pdf():
    return PageDecodeError("page decoding refused: encrypted PDF",
                           "encrypted_pdf")


def _require_pdf_eof(data):
    # PDFium can repair a file missing its final %%EOF, so opening
    # alone is not truncation validation. Bounded check only: allow
    # lawful trailing whitespace, require the EOF marker there. This
    # is not a universal hostile-PDF validator.
    if not data.rstrip(b"\x00\t\r\n\x0c ").endswith(b"%%EOF"):
        raise _malformed_pdf()


def _require_finite_positive(width, height):
    for value in (width, height):
        if (isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            _refuse("invalid_dimensions",
                    "page decoding refused: invalid page dimensions")


def _validate_pdf_render_size(page_width, page_height):
    _require_finite_positive(page_width, page_height)
    scaled_width = page_width * _PDF_SCALE
    scaled_height = page_height * _PDF_SCALE
    if not (math.isfinite(scaled_width)
            and math.isfinite(scaled_height)):
        _refuse("image_too_large",
                "page decoding refused: page dimensions exceed limits")
    # Same ceiling PDFium's render applies; checked before any
    # bitmap is allocated.
    width = math.ceil(scaled_width)
    height = math.ceil(scaled_height)
    if max(width, height) > MAX_EDGE_PIXELS or width * height > MAX_PIXELS:
        _refuse("image_too_large",
                "page decoding refused: page dimensions exceed limits")
    return width, height


def _close_quietly(resource):
    if resource is not None:
        try:
            resource.close()
        except Exception:
            pass


def _load_refusal(data):
    return _encrypted_pdf() if b"/Encrypt" in data else _malformed_pdf()


def _close_failed_construction(document, raw):
    # Disarm the wrapper finalizer if construction installed one,
    # so this direct close is the handle's only effective close.
    # The strong `document` reference held by the caller prevents
    # the finalizer from firing before it can be detached here.
    finalizer = getattr(document, "_finalizer", None)
    if finalizer is not None and finalizer.alive:
        finalizer.detach()
        document._finalizer = None
    pdfium_raw.FPDF_CloseDocument(raw)
    document.raw = None


def _acquire_pdf_document(data):
    """Load a document with exactly one cleanup authority throughout.

    The bytes constructor cannot be used: the library loads a raw
    document in ``_open_pdf`` and rejects a page count below one
    before installing the wrapper finalizer, so that handle would
    be lost. ``PdfDocument`` also accepts a raw ``FPDF_DOCUMENT``
    handle. The wrapper object is allocated before any native
    acquisition (an allocation failure therefore owns nothing and
    only needs the typed refusal) and held by strong reference
    across load, validation, and ``__init__``: return-assignment
    would not prove transfer, because the constructor installs its
    finalizer before a later step (ObjectTracker registration)
    can still fail. While only the raw handle exists this function
    is sole owner and closes it on validation failure; on
    ``__init__`` failure, ownership is read from the held object's
    actual finalizer state, the finalizer is detached if armed,
    and the raw handle is closed here exactly once (under the
    caller's lock). On success, the wrapper is the sole owner.
    The caller's frame keeps ``data`` referenced until the
    wrapper is closed.
    """
    try:
        document = object.__new__(pdfium.PdfDocument)
    except Exception:
        raise _load_refusal(data) from None
    try:
        raw = pdfium_raw.FPDF_LoadMemDocument64(
            data, len(data), None)
    except Exception:
        raise _load_refusal(data) from None
    if not raw:
        raise _load_refusal(data)
    try:
        if pdfium_raw.FPDF_GetPageCount(raw) < 1:
            raise _load_refusal(data)
    except PageDecodeError:
        pdfium_raw.FPDF_CloseDocument(raw)
        raise
    except Exception:
        pdfium_raw.FPDF_CloseDocument(raw)
        raise _load_refusal(data) from None
    try:
        pdfium.PdfDocument.__init__(document, raw)
    except Exception:
        _close_failed_construction(document, raw)
        raise _load_refusal(data) from None
    except BaseException:
        _close_failed_construction(document, raw)
        raise
    return document


def _render_pdf_page(data, page_index):
    """Render one PDF page; every PDFium call holds _PDFIUM_LOCK."""
    with _PDFIUM_LOCK:
        document = None
        page = None
        bitmap = None
        view = None
        owned = None
        try:
            document = _acquire_pdf_document(data)
            try:
                revision = pdfium_raw.FPDF_GetSecurityHandlerRevision(
                    document.raw)
            except Exception:
                raise _malformed_pdf() from None
            if revision != -1:
                raise _encrypted_pdf()
            try:
                page_count = len(document)
            except Exception:
                raise _malformed_pdf() from None
            if type(page_count) is not int or page_count < 1:
                raise _malformed_pdf()
            if page_index >= page_count:
                _refuse("invalid_page_index",
                        "page decoding refused: unsupported page index")
            try:
                page = document.get_page(page_index)
                page_width, page_height = page.get_size()
                crop_left, crop_bottom, crop_right, crop_top = (
                    page.get_cropbox())
                crop_width = crop_right - crop_left
                crop_height = crop_top - crop_bottom
            except Exception:
                raise _malformed_pdf() from None
            # get_size() alone is not enough: PDFium substitutes a
            # default page size for a degenerate crop box, and such a
            # page must be refused rather than rendered at the
            # substituted size. Hence the raw crop span gets only the
            # finite/positive check; edge/area allocation caps apply
            # to get_size(), the actual native rendered extent (crop
            # intersected and rotation applied), before any bitmap.
            _require_finite_positive(crop_width, crop_height)
            _validate_pdf_render_size(page_width, page_height)
            try:
                bitmap = page.render(scale=_PDF_SCALE)
                view = bitmap.to_pil()
                # The view may share bitmap memory; copy before any
                # owner closes, then encode from the owned copy.
                owned = view.copy()
                owned.load()
            except Exception:
                raise _malformed_pdf() from None
            width, height = _validate_dimensions(*owned.size)
            out = io.BytesIO()
            try:
                try:
                    owned.save(out, format="PNG")
                except Exception:
                    raise _malformed_pdf() from None
                png = out.getvalue()
            finally:
                out.close()
            return png, width, height, page_count
        finally:
            _close_quietly(owned)
            _close_quietly(view)
            _close_quietly(bitmap)
            _close_quietly(page)
            _close_quietly(document)


def decode_page(data, page_index=0, *, rights_ref=""):
    if type(page_index) is not int or page_index < 0:
        _refuse("invalid_page_index",
                "page decoding refused: unsupported page index")
    if not isinstance(rights_ref, str):
        _refuse("invalid_rights_ref",
                "page decoding refused: invalid rights reference")
    if type(data) is not bytes:
        _refuse("invalid_type",
                "page decoding refused: uploaded data must be bytes")
    if len(data) == 0:
        _refuse("empty_input", "page decoding refused: empty input")
    if len(data) > MAX_ORIGINAL_BYTES:
        _refuse("input_too_large",
                "page decoding refused: uploaded data exceeds size limit")
    if data.startswith(_PDF_SIGNATURE):
        _require_pdf_eof(data)
        png, width, height, page_count = _render_pdf_page(
            data, page_index)
        source = SourcePage(
            source_id=f"page-{uuid.uuid4().hex}",
            source_sha256=hashlib.sha256(data).hexdigest(),
            page_index=page_index,
            render_sha256=hashlib.sha256(png).hexdigest(),
            width=width, height=height, rights_ref=rights_ref,
            coordinate_convention="raster_pixels_top_left")
        return DecodedPage(
            original_bytes=data, png_bytes=png,
            decoded_source=DecodedSourcePage(source),
            media_type=_PDF_MEDIA_TYPE, page_count=page_count,
            decoder=_PDF_DECODER,
            render_dpi=PDF_RENDER_DPI,
            exif_orientation=None,
            orientation_applied=False)
    if page_index != 0:
        _refuse("invalid_page_index",
                "page decoding refused: unsupported page index")

    buffer, image = _open_image(data)
    try:
        try:
            fmt, media_type = _checked_header(image)
            if fmt == "PNG":
                _verify_png_container(image, data)
            else:
                (png, width, height, exif_orientation,
                 orientation_applied) = _decode_and_render(image)
        finally:
            image.close()
    finally:
        buffer.close()

    if fmt == "PNG":
        buffer, image = _open_image(data)
        try:
            try:
                fmt, media_type = _checked_header(image)
                (png, width, height, exif_orientation,
                 orientation_applied) = _decode_and_render(image)
            finally:
                image.close()
        finally:
            buffer.close()

    source = SourcePage(
        source_id=f"page-{uuid.uuid4().hex}",
        source_sha256=hashlib.sha256(data).hexdigest(),
        page_index=page_index,
        render_sha256=hashlib.sha256(png).hexdigest(),
        width=width, height=height, rights_ref=rights_ref,
        coordinate_convention="raster_pixels_top_left")
    return DecodedPage(
        original_bytes=data, png_bytes=png,
        decoded_source=DecodedSourcePage(source),
        media_type=media_type, page_count=1,
        decoder=f"Pillow {PIL.__version__}",
        render_dpi=None,
        exif_orientation=exif_orientation,
        orientation_applied=orientation_applied)
