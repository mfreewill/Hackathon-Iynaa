# Page decoder (image + selected PDF page)

Socket-free decoder for one uploaded page: a PNG/JPEG image, or one
selected page of a PDF rendered at 216 DPI.

## Public API

Module: `teacher_demo.page_decoder`

- `decode_page(data, page_index=0, *, rights_ref="") -> DecodedPage`
- `PageDecodeError(message, code)` — typed, value-free refusal; the
  machine-readable reason is on `error.code`. Messages and codes never
  contain uploaded bytes, paths, or other caller values.
- `DecodedPage` (immutable): `original_bytes`, `png_bytes`,
  `decoded_source`, `media_type`, `page_count`, `decoder`,
  `render_dpi`, `exif_orientation`, `orientation_applied`.
- Bounds: `MAX_ORIGINAL_BYTES = 20 * 1024 * 1024`,
  `MAX_EDGE_PIXELS = 12_000`, `MAX_PIXELS = 20_000_000`.
  PDF pages render at `PDF_RENDER_DPI = 216` (scale 3) and report
  that DPI; image results have `render_dpi is None`.

## Accepted input

- `data` must be exactly `bytes` containing one complete, single-frame
  PNG or JPEG image. `media_type` is `image/png` or `image/jpeg` from
  the actual decoded format, `page_count` is `1`, and `decoder` is the
  actual `Pillow <version>` string.
- Or `data` is actual PDF bytes (`%PDF-...`). Only the selected page
  renders, at 216 DPI with the page's native display semantics.
  `media_type` is `application/pdf`, `page_count` is the document's
  actual page count, `render_dpi` is `216`, and `decoder` is the
  measured `pypdfium2 <version> / PDFium <version>` string.
- `page_index` must be a genuine nonnegative `int` (`bool`, floats
  and fractions are not indices). Images accept only `0`; PDFs accept
  any index below the actual page count.
- `rights_ref` must be a `str`. It is preserved exactly as metadata on
  the `SourcePage`; it is never rights clearance
  (`rights_is_clearance` stays `False`).

Bounds are checked before pixel decoding: at most 20 MiB of original
bytes, finite positive dimensions, at most 12,000 pixels on either
edge, and at most 20,000,000 pixels in total. Pillow's global safety
settings (`Image.MAX_IMAGE_PIXELS`, `ImageFile.LOAD_TRUNCATED_IMAGES`)
are read but never changed. These bounds do not claim hostile-file
memory or time isolation.

## Integrity ordering

Dimension bounds and the single-frame check run on the parsed header,
before any pixel load. For PNG, chunk headers are then walked to the
actual first `IEND`, which must be the complete empty terminal chunk
(length `0`, correct CRC) ending exactly at the end of input; a decoy
`IEND` later in the bytes, trailing data, a corrupt or truncated
actual terminal CRC, or a nonempty `IEND` is refused as
`malformed_image`. Pillow `verify()` then checks the chunks it
visits. The image is closed and reopened, pixels are loaded first,
and only then is EXIF read and applied, so a failed pixel load is
never treated as merely missing EXIF. JPEG follows the same
header/bounds checks and pixel-load-before-EXIF order without the
PNG container step.

For PDF, a bounded completeness check first requires `%%EOF` after
optional trailing whitespace (PDFium alone repairs its absence).
Every PDFium operation — construction, security-handler query, page
count, page load, size/crop-box reads, render, and cleanup — runs
under one process-wide lock. Encrypted documents, including ones
that open with an empty password (security-handler revision other
than `-1`), are refused. Finite positive page/crop-box dimensions
are required (a degenerate crop box is refused, never rendered at
PDFium's substituted default size). Edge/area bounds apply to the
ceiling of the actual native rendered extent — the display size
with crop intersected and page rotation applied — not to a raw
unrendered crop-box span, and are checked before any bitmap is
allocated; the raw crop span receives only the finite/positive
check. The `PdfDocument` wrapper object is allocated before any
native acquisition (so an allocation failure owns nothing), then
documents are acquired as an explicitly owned raw handle
(`FPDF_LoadMemDocument64`), closed here if page-count validation
fails (for example a zero-page document). The wrapper is held by
strong reference across load, validation, and initialization: if
initialization fails — before or after its finalizer is
installed — an armed finalizer is detached and the raw handle is
closed here exactly once, so no armed finalizer outlives an
explicitly closed handle; on success the wrapper is the sole
owner. The source bytes stay referenced until that wrapper
closes. The bitmap-backed Pillow view is copied and
loaded before its owners close; document, page, bitmap, view, and
copy are concretely closed on success and refusal. This is a
bounded completeness check, not a universal hostile-PDF validator.

## Refusals (`PageDecodeError.code`)

| Code | Meaning |
| --- | --- |
| `invalid_page_index` | Not a genuine nonnegative `int`, nonzero for an image, or out of range for the PDF |
| `invalid_rights_ref` | `rights_ref` is not a `str` |
| `invalid_type` | `data` is not exactly `bytes` (e.g. `bytearray`, `int`) |
| `empty_input` | `data` is empty |
| `input_too_large` | More than 20 MiB of original bytes |
| `unsupported_format` | Not PNG/JPEG/PDF |
| `malformed_image` | PNG/JPEG-like bytes that cannot be fully decoded |
| `malformed_pdf` | PDF bytes that are malformed or truncated (including missing `%%EOF`); never used for non-PDF formats |
| `encrypted_pdf` | Encrypted PDF, including empty-password encryption |
| `multiframe_image` | Animated or multi-frame image |
| `invalid_dimensions` | Non-finite, non-integer (images), or non-positive dimensions (incl. PDF page/crop-box points) |
| `image_too_large` | Edge/pixel bounds exceeded (decoded image, or PDF render size) |

There is no fallback image: every refusal raises `PageDecodeError`.

## Returned page

- `original_bytes` is the uploaded bytes exactly.
- `png_bytes` is a newly encoded, complete PNG of the full decoded
  page. No crop, resize, cleanup, OCR, text extraction, Arabic
  normalization, model/SAM action, or approval is performed.
- EXIF orientation (tag 274) is honored with Pillow's
  `ImageOps.exif_transpose`. `exif_orientation` records the integer
  tag value, or `None` when absent; `orientation_applied` is `True`
  exactly for orientations 2-8. The returned PNG does not carry an
  orientation that would be applied twice. Dimensions on the
  `SourcePage` are the dimensions of the returned (oriented) pixels.
- Transparency is retained (for example RGBA alpha and palette
  transparency). Modes that PNG cannot store directly from a JPEG
  (`CMYK`, `YCbCr`) are converted to RGB only so a complete PNG can
  be returned.
- `decoded_source` is `DecodedSourcePage(SourcePage(...))` with a
  fresh server-generated `page-<uuid4 hex>` source id,
  `source_sha256` over the original bytes, `render_sha256` over the
  returned PNG bytes, and coordinate convention
  `raster_pixels_top_left`.
- All opened images and buffers are closed, including on refusal.
  For PDF, the document, page, bitmap, bitmap-backed Pillow view,
  and its owned copy are likewise closed. Returned bytes own their
  data and remain usable afterwards.
- For PDF results, `SourcePage.page_index` is the selected index,
  dimensions are the rendered PNG's actual pixels,
  `exif_orientation is None`, and `orientation_applied` is `False`
  (page display rotation is PDFium's native rendering, not EXIF).

## Not implemented here

Whole-document PDF processing (only the selected page renders), PDF
text extraction, HTTP/client upload, live model calls, and browser
proof.
