"""
Seed the demo instance with synthetic data to exercise the Kustodyan integration.
Run inside the pod:  python manage.py shell < demo/seed.py

Creates: a string custom field "Email"; groups kustodyan-reveal / kustodyan-mask;
three users (alice=reveal, bob=mask, carol=none) with API tokens; and one document
with the Email custom field set — which the pre_save signal protects on write.
"""
import hashlib

from django.contrib.auth.models import Group, User
from django.utils import timezone
from rest_framework.authtoken.models import Token

from documents.models import CustomField, CustomFieldInstance, Document

# 1) custom field (string)
field, _ = CustomField.objects.get_or_create(
    name="Email", defaults={"data_type": CustomField.FieldDataType.STRING})

# 2) role groups
reveal_grp, _ = Group.objects.get_or_create(name="kustodyan-reveal")
mask_grp, _ = Group.objects.get_or_create(name="kustodyan-mask")


def mkuser(name, group):
    u, created = User.objects.get_or_create(username=name, defaults={"is_active": True})
    if created:
        u.set_password(f"Demo-{name}-pw!")
        u.save()
    u.groups.clear()
    if group:
        u.groups.add(group)
    token, _ = Token.objects.get_or_create(user=u)
    return token.key


alice = mkuser("alice", reveal_grp)   # full reveal (R_MANAGER)
bob = mkuser("bob", mask_grp)         # masked (R_OPERATOR)
carol = mkuser("carol", None)         # no access (403)

# 3) a document with the protected custom field — saving the CFI triggers protect-on-write
doc, _ = Document.objects.get_or_create(
    checksum=hashlib.md5(b"kustodyan-demo-doc-1").hexdigest(),
    defaults={"title": "Kustodyan demo invoice", "content": "",
              "mime_type": "application/pdf", "created": timezone.now()},
)
cfi, _ = CustomFieldInstance.objects.update_or_create(
    document=doc, field=field, defaults={"value_text": "laura.smith@example.com"})
cfi.refresh_from_db()

print("=== SEED RESULT ===")
print("DOC_ID:", doc.id)
print("STORED_VALUE_AT_REST:", cfi.value_text)   # should be kproto:<token>
print("ALICE_TOKEN:", alice)
print("BOB_TOKEN:", bob)
print("CAROL_TOKEN:", carol)
