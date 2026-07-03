from django.apps import AppConfig


class PaperlessKustodyanConfig(AppConfig):
    """
    Standalone paperless-ngx add-on app that protects configured document custom-field
    values through the Kustodyan (RegData RPS) Engine on write, and exposes a role-gated
    reveal (deprotect) endpoint. Activated via PAPERLESS_APPS — no paperless source files
    are modified.
    """
    name = "paperless_kustodyan"
    verbose_name = "Kustodyan field protection"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import signals  # noqa: F401  — registers the pre_save receivers
        from . import classifier_patch, preview_patch, search_patch, serializers_patch, urls_patch
        urls_patch.install()
        serializers_patch.install()   # role-aware deprotect in paperless's native API
        search_patch.install()        # tokenized full-text search (searchable mode)
        preview_patch.install()       # role-aware preview/download (rendered from deprotected content)
        classifier_patch.install()    # auto-learn over the deterministic tokens (ML on encrypted data)
