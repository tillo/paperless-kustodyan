"""Render text → a minimal, valid multi-page PDF (no external PDF lib in the paperless image).

Used to generate a document preview ON THE FLY from the deprotected content for the requesting
role — the cleartext is never written to disk, only streamed in the response.
"""
import textwrap


def _esc(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def render_pdf(title, body):
    lines = []
    if title:
        lines += [title, ""]
    for para in (body or "").split("\n"):
        lines += (textwrap.wrap(para, 95) or [""])
    per = 52
    pages = [lines[i:i + per] for i in range(0, len(lines), per)] or [[""]]

    objs = {1: b"<</Type/Catalog/Pages 2 0 R>>",
            3: b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"}
    kids, num = [], 4
    for pl in pages:
        ops = b"BT /F1 11 Tf 50 790 Td 14 TL "
        first = True
        for ln in pl:
            ops += (b"" if first else b"T* ") + b"(" + _esc(ln).encode("latin-1", "replace") + b") Tj "
            first = False
        ops += b"ET"
        pno, cno = num, num + 1
        num += 2
        objs[pno] = b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Resources<</Font<</F1 3 0 R>>>>/Contents %d 0 R>>" % cno
        objs[cno] = b"<</Length %d>>stream\n" % len(ops) + ops + b"\nendstream"
        kids.append(pno)
    objs[2] = b"<</Type/Pages/Kids[" + b" ".join(b"%d 0 R" % k for k in kids) + b"]/Count %d>>" % len(kids)

    pdf, offs = b"%PDF-1.4\n", {}
    for i in sorted(objs):
        offs[i] = len(pdf)
        pdf += b"%d 0 obj" % i + objs[i] + b" endobj\n"
    xref = len(pdf)
    n = max(objs) + 1
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % n
    for i in range(1, n):
        pdf += b"%010d 00000 n \n" % offs.get(i, 0)
    pdf += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (n, xref)
    return pdf


def render_png(title, body, width=1000):
    """Render text → a PNG (Pillow). Used for the on-the-fly preview of an IMAGE document, so the
    frontend's <img> renderer (chosen from the doc's mime_type) gets image bytes, not a PDF."""
    import io
    import textwrap

    from PIL import Image, ImageDraw, ImageFont

    try:
        base = "/usr/share/fonts/truetype/dejavu/DejaVuSans"
        font = ImageFont.truetype(base + ".ttf", 18)
        bold = ImageFont.truetype(base + "-Bold.ttf", 24)
    except Exception:
        font = bold = ImageFont.load_default()

    lines = []
    for para in (body or "").split("\n"):
        lines += textwrap.wrap(para, 92) or [""]
    lh = 26
    img = Image.new("RGB", (width, 90 + lh * max(len(lines), 1)), "white")
    d = ImageDraw.Draw(img)
    y = 30
    if title:
        d.text((40, y), title, fill="black", font=bold)
        y += 48
    for ln in lines:
        d.text((40, y), ln, fill="black", font=font)
        y += lh
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
