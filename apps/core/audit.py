from .models import BusinessAuditLog


def record_business_event(*, actor=None, action, object_type, reference="", summary="", actor_label=""):
    if actor is not None:
        actor_label = actor.get_full_name().strip() or actor.get_username()
    return BusinessAuditLog.objects.create(
        actor=actor,
        actor_label=(actor_label or "Système")[:160],
        action=action[:80],
        object_type=object_type[:80],
        object_reference=str(reference or "")[:120],
        summary=str(summary or "")[:500],
    )
