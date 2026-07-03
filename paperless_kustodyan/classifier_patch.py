"""
Feed paperless's auto-matching classifier only the DETERMINISTIC search tokens of protected
content. `DocumentClassifier.preprocess_content` is the single funnel for both training and
prediction, so patching it keeps them consistent. The probabilistic full-fidelity copy (stored
as the first token) is unique per document — pure noise for the classifier — so we strip it.
"""
import logging

from . import config

log = logging.getLogger("paperless_kustodyan")


def install():
    from documents.classifier import DocumentClassifier

    orig = DocumentClassifier.preprocess_content
    if getattr(orig, "_kustodyan_patched", False):
        return

    def preprocess_content(self, content, *args, **kwargs):
        if isinstance(content, str) and config.is_protected(content):
            body = content[len(config.PROTECT_PREFIX):]
            if config.CONTENT_READ_PROPERTY:
                parts = body.split(" ", 1)         # drop the probabilistic full copy (first token)
                content = parts[1] if len(parts) > 1 else ""
            else:
                content = body
        return orig(self, content, *args, **kwargs)

    preprocess_content._kustodyan_patched = True
    DocumentClassifier.preprocess_content = preprocess_content
    log.info("kustodyan: patched classifier preprocess_content for tokenized auto-learn")
