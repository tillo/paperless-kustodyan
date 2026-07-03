"""
Contextual read endpoints. The heart of the showcase: the SAME document is rendered
differently per caller, decided server-side by the RPS configuration via the caller's role.

  GET /api/kustodyan/document/<id>/   → title, content and tags, each transformed for the
                                        caller's role (Django group → RPS role):
        DATA_STEWARD → full cleartext   CASE_WORKER → masked   STAFF → stored protected token
        (no role)    → 403
"""
import logging

from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from documents.models import Document

from . import config
from .serializers_patch import deprotect

log = logging.getLogger("paperless_kustodyan")


class DocumentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, document_id: int):
        role = config.role_for_user(request.user)
        if not role:
            raise PermissionDenied("You have no Kustodyan role; protected fields are not visible.")
        try:
            doc = Document.objects.prefetch_related("tags").get(pk=document_id)
        except Document.DoesNotExist:
            raise NotFound()

        user = request.user.get_username()
        log.info("kustodyan: document %s rendered for %s as role %s", doc.pk, user, role)
        resp = Response({
            "document": doc.pk,
            "title": deprotect(doc.title, "title", role),
            "viewer": user,
            "role": role,
            "content": deprotect(doc.content, "content", role),
            "tags": [deprotect(t.name, "tag", role) for t in doc.tags.all()],
        })
        resp["Cache-Control"] = "no-store"   # deprotected cleartext — never cache it
        return resp
