"""
Role-aware document preview/download. paperless's `preview`/`download` actions both go through
`DocumentViewSet.file_response`, which serves a static file. We monkeypatch it so that, for a
protected document, a role that can REVEAL the content gets the deprotected text rendered on the fly
in the document's NATIVE type — image for an image doc (so the frontend's <img> works), plain text
for text/plain, PDF otherwise (steward → full, worker → masked); read-only / no-role callers fall
through to the original (the neutral placeholder, or a 403 if they lack view permission).

The cleartext is never persisted — it is generated transiently for the request.
"""
import logging

from django.http import HttpResponse, HttpResponseForbidden

from . import config
from .pdf_render import render_pdf, render_png

log = logging.getLogger("paperless_kustodyan")


def install():
    from documents.views import DocumentViewSet

    orig = DocumentViewSet.file_response
    if getattr(orig, "_kustodyan_patched", False):
        return

    def file_response(self, pk, request, disposition):
        try:
            from documents.models import Document
            from documents.permissions import has_perms_owner_aware

            from .serializers_patch import deprotect

            doc = Document.global_objects.select_related("owner").get(id=pk)
            user = getattr(request, "user", None)
            role = config.role_for_user(user) if (user is not None and getattr(user, "is_authenticated", False)) else None
            # only roles that actually transform (not the read-only STAFF tier) get a rendered preview
            if config.is_protected(doc.content) and role and role != config.STAFF_ROLE:
                if not has_perms_owner_aware(user, "view_document", doc):
                    return HttpResponseForbidden("Insufficient permissions")
                content = deprotect(doc.content, "content", role)
                title = deprotect(doc.title, "title", role) if config.is_protected(doc.title) else doc.title
                # render the revealed content in the document's NATIVE type — the frontend picks its
                # viewer from the doc's mime_type, so an image doc needs image bytes (a PDF → broken <img>)
                mime = (doc.mime_type or "").lower()
                if mime.startswith("image/"):
                    body, ctype, fn = render_png(title, content), "image/png", "document.png"
                elif mime == "text/plain":
                    head = (title + "\n\n") if title else ""
                    body, ctype, fn = (head + (content or "")).encode("utf-8"), "text/plain; charset=utf-8", "document.txt"
                else:
                    body, ctype, fn = render_pdf(title, content), "application/pdf", "document.pdf"
                resp = HttpResponse(body, content_type=ctype)
                resp["Content-Disposition"] = f'{disposition}; filename="{fn}"'
                resp["Cache-Control"] = "no-store"   # deprotected cleartext — never cache it
                return resp
        except Exception as e:  # never break the endpoint — fall back to the original file
            log.warning("kustodyan: role-aware preview failed: %s", e)
        return orig(self, pk, request, disposition)

    file_response._kustodyan_patched = True
    DocumentViewSet.file_response = file_response
    log.info("kustodyan: patched DocumentViewSet.file_response for role-aware preview")
