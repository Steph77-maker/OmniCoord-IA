"""Opérations privilégiées de la plateforme OmniCoord.

Aucune utilisation de service_role ne doit reposer uniquement sur une valeur
`st.session_state['is_admin']`. L'identité est relue depuis le JWT Supabase puis
le statut admin est revalidé côté base avant chaque opération privilégiée.
"""
from __future__ import annotations

import logging

from .database import current_authenticated_user_id, get_supabase_admin
from .exceptions import AuthorizationError

logger = logging.getLogger("omnicoord.admin")


def require_platform_admin() -> str:
    user_id = current_authenticated_user_id()
    if not user_id:
        raise AuthorizationError("Session Supabase invalide")
    try:
        res = (
            get_supabase_admin()
            .table("profils")
            .select("est_admin")
            .eq("id", user_id)
            .single()
            .execute()
        )
        if not res.data or not bool(res.data.get("est_admin", False)):
            raise AuthorizationError("Privilèges administrateur requis")
        return user_id
    except AuthorizationError:
        raise
    except Exception as exc:
        logger.exception("Admin revalidation failed")
        raise AuthorizationError("Impossible de vérifier les privilèges administrateur") from exc


def create_auth_user(email: str, password: str):
    require_platform_admin()
    return get_supabase_admin().auth.admin.create_user({
        "email": email.strip().lower(),
        "password": password,
        "email_confirm": True,
    })


def delete_auth_user(user_id: str) -> None:
    require_platform_admin()
    get_supabase_admin().auth.admin.delete_user(str(user_id))
