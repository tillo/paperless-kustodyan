"""
Expanded showcase seed (run: python manage.py shell < demo/showcase_seed.py)

  - 4 role users (steward/worker/clerk/nobody), pw Demo-<name>!
  - protected CORRESPONDENTS (role-aware) + a protected CUSTOM FIELD (IBAN, role-aware)
  - documents in 4 FORMATS (pdf / png / txt / docx) with real placeholder files + thumbnails
  - sensitive tags, correspondents and a tag set to AUTO matching → trains paperless's classifier
    over the *tokenized* content, then predicts a held-out document (ML on encrypted data)
"""
import io
import os
import uuid as _uuid
import zipfile

from django.conf import settings
from django.contrib.auth.models import Group, Permission, User
from django.utils import timezone
from PIL import Image, ImageDraw
from rest_framework.authtoken.models import Token

from documents.classifier import DocumentClassifier
from documents.models import (Correspondent, CustomField, CustomFieldInstance,
                              Document, MatchingModel, Tag)

# ---------- users ----------
GROUPS = {g: Group.objects.get_or_create(name=g)[0]
          for g in ("kustodyan-steward", "kustodyan-worker", "kustodyan-staff")}
_view_perms = list(Permission.objects.filter(content_type__app_label="documents", codename__startswith="view_"))


def mkuser(name, group):
    u, created = User.objects.get_or_create(username=name, defaults={"is_active": True})
    if created:
        u.set_password(f"Demo-{name}!"); u.save()
    u.groups.clear()
    if group:
        u.groups.add(GROUPS[group])
    if name != "nobody":
        u.user_permissions.set(_view_perms)
    return Token.objects.get_or_create(user=u)[0].key


tokens = {
    "steward (DATA_STEWARD - full)": mkuser("steward", "kustodyan-steward"),
    "worker  (CASE_WORKER - masked)": mkuser("worker", "kustodyan-worker"),
    "clerk   (STAFF - read token)": mkuser("clerk", "kustodyan-staff"),
    "nobody  (no role - denied)": mkuser("nobody", None),
}

# ---------- placeholder files (neutral — never the cleartext original) ----------
_MSG = ["Protected by Kustodyan", "Content is encrypted by RegData RPS.",
        "Open the document to see it rendered for your role."]


def _pdf():
    t = ("BT /F1 22 Tf 60 740 Td 26 TL (%s) Tj /F1 12 Tf T* (%s) Tj T* (%s) Tj ET" % tuple(_MSG)).encode("latin-1", "replace")
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
            b"<</Length " + str(len(t)).encode() + b">>stream\n" + t + b"\nendstream"]
    pdf, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(pdf)); pdf += str(i).encode() + b" 0 obj" + o + b" endobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offs:
        pdf += ("%010d 00000 n \n" % off).encode()
    pdf += b"trailer<</Size " + str(len(objs) + 1).encode() + b"/Root 1 0 R>>\nstartxref\n" + str(xref).encode() + b"\n%%EOF"
    return pdf


def _png():
    img = Image.new("RGB", (760, 1000), "white"); d = ImageDraw.Draw(img)
    d.rectangle([12, 12, 747, 987], outline=(150, 150, 150), width=3)
    for i, line in enumerate(_MSG):
        d.text((40, 50 + i * 30), line, fill=(40, 40, 40) if i == 0 else (110, 110, 110))
    b = io.BytesIO(); img.save(b, "PNG"); return b.getvalue()


def _txt():
    return ("\n".join(_MSG) + "\n").encode("utf-8")


def _docx():
    b = io.BytesIO()
    z = zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED)
    z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    paras = "".join("<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % m for m in _MSG)
    z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>%s</w:body></w:document>' % paras)
    z.close(); return b.getvalue()


def _thumb():
    img = Image.new("RGB", (430, 600), "white"); d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 419, 589], outline=(150, 150, 150), width=2)
    d.text((28, 36), "PROTECTED", fill=(40, 40, 40)); d.text((28, 62), "Kustodyan / RegData RPS", fill=(110, 110, 110))
    b = io.BytesIO(); img.save(b, "WEBP"); return b.getvalue()


FILES = {"pdf": _pdf(), "png": _png(), "txt": _txt(), "docx": _docx()}
MIME = {"pdf": "application/pdf", "png": "image/png", "txt": "text/plain",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
THUMB = _thumb()

# ---------- reset (clean slate) ----------
Document.objects.all().delete()
Tag.objects.all().delete()
Correspondent.objects.all().delete()
CustomField.objects.filter(name="IBAN").delete()

# ---------- correspondents (AUTO → classifier learns them) ----------
corr = {n: Correspondent.objects.create(name=n, matching_algorithm=MatchingModel.MATCH_AUTO)
        for n in ("Zurich University Hospital", "Acme Corp Payroll", "Acme Corp Billing")}
iban_field = CustomField.objects.create(name="IBAN", data_type=CustomField.FieldDataType.STRING)
AUTO_TAGS = {"medical", "salary", "invoice"}


_tag_cache = {}


def tag(name):
    # cache by cleartext name: a protected (deterministic) tag name can't be looked up by get_or_create,
    # and re-creating it would collide on the unique token — so we create once per run and reuse.
    if name not in _tag_cache:
        t = Tag.objects.create(name=name)  # pre_save protects sensitive names
        if name in AUTO_TAGS:
            t.matching_algorithm = MatchingModel.MATCH_AUTO
            t.save()
        _tag_cache[name] = t
    return _tag_cache[name]


# (title, content, format, tags, correspondent, iban)
DOCS = [
    ("Discharge summary - J. Doe", "Patient John Doe, DOB 1980-02-11, diagnosis hypertension, prescribed ramipril 5mg, cardiology follow-up in three months.", "pdf", ["medical", "patient"], "Zurich University Hospital", None),
    ("Lab results - M. Roe", "Patient Mary Roe blood test cholesterol elevated, fasting glucose normal, hospital referral to cardiology clinic.", "png", ["medical", "patient"], "Zurich University Hospital", None),
    ("Radiology report - K. Lee", "Patient Karl Lee chest x-ray no acute findings, mild scoliosis noted, routine clinical follow-up advised.", "txt", ["medical"], "Zurich University Hospital", None),
    ("Employment contract - A. Smith", "Anna Smith role Engineer annual salary CHF 145000 strictly confidential payroll start 2026-08-01.", "pdf", ["hr", "salary", "confidential"], "Acme Corp Payroll", "CH9300762011623852957"),
    ("Payslip 2026-06 - A. Smith", "Anna Smith gross salary CHF 12083 net CHF 9650 pension contribution confidential payroll department.", "docx", ["hr", "salary", "confidential"], "Acme Corp Payroll", "CH9300762011623852957"),
    ("Bonus letter - B. Jones", "Bob Jones annual bonus CHF 20000 awarded confidential salary review payroll compensation.", "txt", ["hr", "salary"], "Acme Corp Payroll", "CH5604835012345678009"),
    ("Invoice 2024-118 - Acme", "Invoice 2024-118 from Acme Corp total CHF 4500 consulting services billing due 2026-07-31.", "pdf", ["invoice"], "Acme Corp Billing", None),
    ("Invoice 2024-204 - Acme", "Invoice 2024-204 from Acme Corp total CHF 7800 software licenses billing due 2026-08-15.", "png", ["invoice"], "Acme Corp Billing", None),
]

os.makedirs(settings.ORIGINALS_DIR, exist_ok=True)
os.makedirs(settings.THUMBNAIL_DIR, exist_ok=True)

doc_ids = []
for title, content, fmt, tagnames, corr_name, iban in DOCS:
    doc = Document.objects.create(
        checksum=f"kustodyan-{_uuid.uuid4().hex}", title=title, content=content,
        mime_type=MIME[fmt], correspondent=corr[corr_name], created=timezone.now())
    doc.tags.set([tag(t) for t in tagnames])
    if iban:
        CustomFieldInstance.objects.create(document=doc, field=iban_field, value_text=iban)
    with open(str(doc.source_path), "wb") as f:
        f.write(FILES[fmt])
    with open(str(doc.thumbnail_path), "wb") as f:
        f.write(THUMB)
    doc_ids.append(doc.id)

# ---------- train the classifier over the TOKENIZED content (BEFORE the held-out doc exists,
# else paperless labels the unfiled doc -1 and the model memorises it) ----------
clf = DocumentClassifier()
trained = False
try:
    trained = clf.train()
    if trained:
        clf.save()
except Exception as e:
    print("classifier train error:", e)

# held-out doc (created AFTER training → genuinely unseen) for the classifier to predict
test = Document.objects.create(
    checksum=f"kustodyan-{_uuid.uuid4().hex}", title="Unfiled note - new patient",
    content="Patient new admission, hypertension follow-up, cardiology clinic referral, prescribed medication.",
    mime_type="application/pdf", created=timezone.now())
with open(str(test.source_path), "wb") as f:
    f.write(FILES["pdf"])
with open(str(test.thumbnail_path), "wb") as f:
    f.write(THUMB)

print("=== SHOWCASE SEED ===")
print("DOC_IDS:", doc_ids, "TEST_DOC:", test.id)
for label, key in tokens.items():
    print(f"TOKEN {label} = {key}")
print("CLASSIFIER trained:", trained)
if trained:
    from paperless_kustodyan.serializers_patch import deprotect

    def _scalar(v):
        return int(v[0]) if hasattr(v, "__len__") and len(v) else (int(v) if v is not None else None)

    pc = _scalar(clf.predict_correspondent(test.content))
    pt = [int(i) for i in clf.predict_tags(test.content)]
    corr_name = deprotect(Correspondent.objects.get(pk=pc).name, "correspondent", "DATA_STEWARD") if pc else None
    tag_names = [deprotect(Tag.objects.get(pk=i).name, "tag", "DATA_STEWARD") for i in pt]
    print("PREDICT for held-out medical note -> correspondent:", corr_name, "| tags:", tag_names)
