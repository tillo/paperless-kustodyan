"""
Protect-on-write: pre_save receivers that transform sensitive values through the Kustodyan
Engine BEFORE they are persisted, so cleartext never reaches the database.

  - Document.content     → `content_full` display copy + `content` deterministic tokens (equality search)
  - Document.title       → `title_full` display copy + `title_search` PROPE tokens (equality + begins-with)
  - Tag.name (sensitive) → `tag` deterministic tokens (equality search)
  - Correspondent.name   → `correspondent` display copy + `correspondent_search` PROPE tokens (eq + begins-with)
  - CustomFieldInstance  → `customfield` probabilistic full-fidelity copy (string/text; not searched)

pre_save mutates the instance in place (no extra .save() → no recursion); the prefix marker
prevents re-protecting. Fail-closed: a transform error aborts the save (never store cleartext).
"""
import logging

from django.db.models.signals import pre_save
from django.dispatch import receiver

from documents.models import Correspondent, CustomField, CustomFieldInstance, Document, Tag

from . import config

log = logging.getLogger("paperless_kustodyan")


def _attrs(log_attrs):
    a = [{"name": k, "value": str(v)} for k, v in log_attrs.items() if v]
    a.append({"name": "app", "value": "paperless"})
    return a


def _search_tokens(text, prop):
    """Deterministic word-by-word search tokens (`k`+hex) for content: same word → same token, so
    the encrypted index is equality-searchable. De-duplicated + batched; property-scoped."""
    words = config.WORD_RE.findall(text.lower())
    if not words:
        return ""
    if config.SEARCH_SALT:
        # SUFFIX salt held by the app, separate from the engine key, so reversing a content token
        # needs BOTH. Write-only (display reads the full-fidelity copy) → no read-side un-salting.
        words = [w + config.SEARCH_SALT for w in words]
    return " ".join(config.encode_token(t) for t in config.protect_words(words, prop))


def _protect_blob(value, prop, **log_attrs):
    """One probabilistic full-fidelity token — exact value preserved, role-aware on read."""
    tok = config.client().protect(value, prop, role=config.PROTECT_ROLE, logging_attrs=_attrs(log_attrs))
    return config.PROTECT_PREFIX + tok


def _protect_searchable(value, display_prop, search_prop, field):
    """Hybrid: encode_blob(probabilistic display copy, sorts below the search tokens) + non-deterministic
    PROPE word tokens (range-searchable: equality + begins-with; frequency hidden). display = first token."""
    blob = config.client().protect(value, display_prop, role=config.PROTECT_ROLE, logging_attrs=_attrs({"field": field}))
    parts = [config.encode_blob(blob)]
    if search_prop:
        words = list(dict.fromkeys(config.WORD_RE.findall(value.lower())))
        if words:
            toks = config.client().protect_many(words, search_prop, role=config.PROTECT_ROLE)
            parts += [config.encode_search(t) for t in toks]
    return config.PROTECT_PREFIX + " ".join(parts)


def _protect(value, prop, **log_attrs):
    """Searchable mode: store lowercased word tokens. Blob mode: one probabilistic token."""
    if config.SEARCHABLE_CONTENT:
        s = _search_tokens(value, prop)
        return config.PROTECT_PREFIX + s if s else config.PROTECT_PREFIX
    return _protect_blob(value, prop, **log_attrs)


@receiver(pre_save, sender=Document, dispatch_uid="kustodyan_protect_document")
def protect_document_on_write(sender, instance: Document, **kwargs):
    content = instance.content
    if config.PROTECT_DOCUMENT_CONTENT and content and not config.is_protected(content):
        try:
            if config.CONTENT_READ_PROPERTY:
                # full-fidelity: probabilistic full copy (display) as the FIRST token, then search words
                full = config.client().protect(content, config.CONTENT_READ_PROPERTY,
                                               role=config.PROTECT_ROLE, logging_attrs=_attrs({"field": "content"}))
                search = _search_tokens(content, config.CONTENT_PROPERTY)
                instance.content = config.PROTECT_PREFIX + full + ((" " + search) if search else "")
            else:
                instance.content = _protect(content, config.CONTENT_PROPERTY, field="content")
        except Exception as e:
            log.error("kustodyan: protect document content failed: %s", e)
            raise
        log.info("kustodyan: protected document content before write")

    title = instance.title
    if config.PROTECT_TITLE and title and not config.is_protected(title):
        try:
            instance.title = _protect_searchable(title, config.TITLE_PROPERTY, config.TITLE_SEARCH_PROPERTY, "title")
        except Exception as e:
            log.error("kustodyan: protect document title failed: %s", e)
            raise


@receiver(pre_save, sender=Tag, dispatch_uid="kustodyan_protect_tag")
def protect_tag_on_write(sender, instance: Tag, **kwargs):
    name = instance.name
    if not name or config.is_protected(name) or name not in config.SENSITIVE_TAGS:
        return
    try:
        instance.name = _protect(name, config.TAG_PROPERTY, field="tag")
    except Exception as e:
        log.error("kustodyan: protect tag failed: %s", e)
        raise
    log.info("kustodyan: protected sensitive tag before write")


@receiver(pre_save, sender=Correspondent, dispatch_uid="kustodyan_protect_correspondent")
def protect_correspondent_on_write(sender, instance: Correspondent, **kwargs):
    name = instance.name
    if not config.PROTECT_CORRESPONDENTS or not name or config.is_protected(name):
        return
    try:
        instance.name = _protect_searchable(name, config.CORRESPONDENT_PROPERTY, config.CORRESPONDENT_SEARCH_PROPERTY, "correspondent")
    except Exception as e:
        log.error("kustodyan: protect correspondent failed: %s", e)
        raise
    log.info("kustodyan: protected correspondent before write")


@receiver(pre_save, sender=CustomFieldInstance, dispatch_uid="kustodyan_protect_custom_field")
def protect_custom_field_on_write(sender, instance: CustomFieldInstance, **kwargs):
    field = instance.field
    if field is None or field.data_type not in (CustomField.FieldDataType.STRING, CustomField.FieldDataType.LONG_TEXT):
        return
    # legacy per-field-name map wins; otherwise the default customfield property if globally enabled
    prop = config.PROTECTED_CUSTOM_FIELDS.get(field.name) or (config.CUSTOMFIELD_PROPERTY if config.PROTECT_CUSTOM_FIELDS else None)
    if not prop:
        return
    attr = CustomFieldInstance.get_value_field_name(field.data_type)
    value = getattr(instance, attr)
    if not value or config.is_protected(value):
        return
    try:
        setattr(instance, attr, _protect_blob(value, prop, field=field.name))
    except Exception as e:
        log.error("kustodyan: protect custom field %r failed: %s", field.name, e)
        raise
