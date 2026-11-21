"""Audit log writer (D82). Every recorded change goes through `log_event`."""

from decimal import Decimal

from .models import AuditLog


def _plain(value):
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return str(value)
    return value


def log_event(actor, action: str, target, summary: str, before=None, after=None, reason: str = "") -> AuditLog:
    """Append one entry. `target` is a model instance (or None); before/after are small dicts."""
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        target_type=target._meta.label_lower if target is not None else "",
        target_id=getattr(target, "pk", None),
        summary=summary[:255],
        before=_plain(before),
        after=_plain(after),
        reason=reason,
    )
