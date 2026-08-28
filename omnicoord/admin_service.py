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
    """Ancien flux conservé pour compatibilité interne, sans usage dans l'onboarding commercial."""
    require_platform_admin()
    return get_supabase_admin().auth.admin.create_user({
        "email": email.strip().lower(),
        "password": password,
        "email_confirm": True,
    })


def create_invited_auth_user(email: str) -> tuple[str, str]:
    """Crée un utilisateur invité et retourne (user_id, token_hash).

    Le mot de passe n'est jamais choisi par l'administrateur. Supabase génère
    un token d'invitation à usage unique ; OmniCoord l'envoie ensuite via la
    messagerie déjà configurée par l'administrateur.
    """
    require_platform_admin()
    normalized_email = email.strip().lower()
    if not normalized_email:
        raise ValueError("Email client manquant")

    response = get_supabase_admin().auth.admin.generate_link({
        "type": "invite",
        "email": normalized_email,
    })

    user = getattr(response, "user", None)
    properties = getattr(response, "properties", None)

    # Compatibilité prudente avec les différentes représentations des réponses
    # GoTrue/Supabase Python (objets Pydantic ou dictionnaires).
    if user is None and isinstance(response, dict):
        user = response.get("user")
    if properties is None and isinstance(response, dict):
        properties = response.get("properties")

    user_id = getattr(user, "id", None)
    if user_id is None and isinstance(user, dict):
        user_id = user.get("id")

    token_hash = getattr(properties, "hashed_token", None)
    if token_hash is None and isinstance(properties, dict):
        token_hash = properties.get("hashed_token")

    if not user_id or not token_hash:
        logger.error("Réponse Supabase d'invitation incomplète")
        raise RuntimeError("Supabase n'a pas retourné les informations d'invitation attendues")

    return str(user_id), str(token_hash)


def delete_auth_user(user_id: str) -> None:
    require_platform_admin()
    get_supabase_admin().auth.admin.delete_user(str(user_id))
