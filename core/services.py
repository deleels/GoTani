"""Farm profile and staff management (D65, D72). OWNER-only writes."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q

from farm.bootstrap import ROLE_OWNER, ROLES, user_has_role

from .models import FarmProfile, UserProfile

PROFILE_FIELDS = ("name", "address", "phone", "latitude", "longitude", "logo")


def get_farm_profile() -> FarmProfile:
    profile, _ = FarmProfile.objects.get_or_create(pk=1)
    return profile


def get_user_profile(user) -> UserProfile:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def require_owner(actor):
    if not user_has_role(actor, ROLE_OWNER):
        raise PermissionDenied("Hanya OWNER yang dapat melakukan ini.")


def update_farm_profile(actor, **fields) -> FarmProfile:
    require_owner(actor)
    unknown = set(fields) - set(PROFILE_FIELDS)
    if unknown:
        raise ValueError(f"Unknown fields: {sorted(unknown)}")
    lat, lon = fields.get("latitude"), fields.get("longitude")
    if (lat is None) != (lon is None):
        raise ValidationError("Lintang dan bujur harus diisi keduanya atau dikosongkan keduanya.")
    if lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValidationError("Koordinat di luar jangkauan.")
    profile = get_farm_profile()
    for key, value in fields.items():
        setattr(profile, key, value)
    profile.full_clean()
    profile.save()
    return profile


def user_role(user) -> str | None:
    if user.is_superuser:
        return ROLE_OWNER
    names = set(user.groups.values_list("name", flat=True))
    return next((r for r in ROLES if r in names), None)


def active_owner_count() -> int:
    User = get_user_model()
    return (
        User.objects.filter(is_active=True)
        .filter(Q(is_superuser=True) | Q(groups__name=ROLE_OWNER))
        .distinct()
        .count()
    )


def _is_last_active_owner(user) -> bool:
    return user.is_active and user_role(user) == ROLE_OWNER and active_owner_count() <= 1


def _check_role(role):
    if role not in ROLES:
        raise ValidationError("Peran tidak dikenal.")


def _set_temp_password(user, password):
    try:
        validate_password(password, user)
    except ValidationError as exc:
        raise ValidationError(exc.messages)
    user.set_password(password)


@transaction.atomic
def create_staff_user(actor, *, username, full_name, role, temp_password):
    require_owner(actor)
    _check_role(role)
    User = get_user_model()
    username = (username or "").strip()
    if not username:
        raise ValidationError("Nama pengguna wajib diisi.")
    if User.objects.filter(username__iexact=username).exists():
        raise ValidationError("Nama pengguna sudah dipakai.")
    first, _, last = (full_name or "").strip().partition(" ")
    user = User(username=username, first_name=first, last_name=last)
    _set_temp_password(user, temp_password)
    user.save()
    user.groups.set([Group.objects.get_or_create(name=role)[0]])
    profile = get_user_profile(user)
    profile.must_change_password = True
    profile.save(update_fields=["must_change_password"])
    return user


@transaction.atomic
def set_staff_role(actor, user, role):
    require_owner(actor)
    _check_role(role)
    if user.is_superuser:
        raise ValidationError("Peran superuser diatur lewat admin Django.")
    if role != ROLE_OWNER and _is_last_active_owner(user):
        raise ValidationError("Tidak dapat mengubah peran OWNER aktif terakhir.")
    user.groups.set([Group.objects.get_or_create(name=role)[0]])
    return user


@transaction.atomic
def set_staff_active(actor, user, active: bool):
    require_owner(actor)
    if not active:
        if user.pk == actor.pk:
            raise ValidationError("Anda tidak dapat menonaktifkan akun Anda sendiri.")
        if _is_last_active_owner(user):
            raise ValidationError("Tidak dapat menonaktifkan OWNER aktif terakhir.")
    user.is_active = active
    user.save(update_fields=["is_active"])
    return user


@transaction.atomic
def reset_staff_password(actor, user, temp_password):
    require_owner(actor)
    _set_temp_password(user, temp_password)
    user.save(update_fields=["password"])
    profile = get_user_profile(user)
    profile.must_change_password = True
    profile.save(update_fields=["must_change_password"])
    return user


def mark_password_changed(user):
    UserProfile.objects.filter(user=user).update(must_change_password=False)
