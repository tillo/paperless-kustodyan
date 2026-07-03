"""
paperless-ngx has no plugin hook to add API URLs (its root urlconf is built statically),
so we INSERT our endpoint at the FRONT of `paperless.urls.urlpatterns` from
AppConfig.ready() — paperless's top-level `^api/` route and its `.*` SPA catch-all are
checked first and would otherwise swallow `api/kustodyan/...`. Idempotent.
"""
import logging

from django.urls import clear_url_caches, include, path

log = logging.getLogger("paperless_kustodyan")


def _installed(urlpatterns) -> bool:
    for grp in urlpatterns:
        for u in getattr(grp, "url_patterns", []):
            if getattr(u, "name", None) == "kustodyan_document":
                return True
    return False


def install():
    from paperless import urls as paperless_urls

    from .views import DocumentView

    if _installed(paperless_urls.urlpatterns):
        return
    paperless_urls.urlpatterns.insert(0,
        path("api/kustodyan/", include([
            path("document/<int:document_id>/", DocumentView.as_view(), name="kustodyan_document"),
        ]))
    )
    clear_url_caches()
    log.info("kustodyan: registered /api/kustodyan/document/<id>/")
