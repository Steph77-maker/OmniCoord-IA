"""Accès Supabase centralisé pour OmniCoord IA.

Point important : le client utilisateur n'est PAS mis en cache globalement.
Le client Supabase contient un état d'authentification mutable ; le partager entre
plusieurs sessions Streamlit peut mélanger les sessions/JWT. On le conserve donc
uniquement dans le ``st.session_state`` de la session navigateur courante.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import streamlit as st
from supabase import Client, create_client

from .exceptions import DatabaseError

logger = logging.getLogger("omnicoord.database")

_USER_CLIENT_KEY = "_omnicoord_supabase_client"


def _create_anon_client() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


def get_supabase() -> Client:
    """Retourne le client Supabase propre à la session Streamlit courante."""
    client = st.session_state.get(_USER_CLIENT_KEY)
    if client is None:
        client = _create_anon_client()
        st.session_state[_USER_CLIENT_KEY] = client
    return client


@st.cache_resource
def get_supabase_admin() -> Client:
    """Client service_role serveur.

    Ce client n'embarque pas de session utilisateur et peut être partagé côté
    serveur. Il contourne RLS : son usage doit rester limité aux opérations
    explicitement privilégiées (audit, auth admin, anti-abus).
    """
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_SERVICE_KEY"],
    )


class _SessionSupabaseProxy:
    """Façade de compatibilité pour le code UI historique (`sb.auth`, etc.)."""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_supabase(), name)


sb = _SessionSupabaseProxy()


def reset_user_client() -> None:
    """Supprime le client utilisateur de la session courante."""
    st.session_state.pop(_USER_CLIENT_KEY, None)


def _generic_db_error(operation: str, table: str) -> None:
    logger.exception("%s failed on table=%s", operation, table)


def sb_select(
    table: str,
    filters: dict | None = None,
    eq_col: str | None = None,
    eq_val=None,
    order: str | None = None,
    limit: int | None = None,
    *,
    strict: bool = False,
) -> pd.DataFrame:
    """SELECT via le client utilisateur/RLS.

    `strict=True` permet aux futurs services métier de distinguer une panne DB
    d'un résultat vide. Le mode compatibilité garde un DataFrame vide pour ne pas
    casser les écrans historiques pendant la migration.
    """
    try:
        q = get_supabase().table(table).select("*")
        if filters:
            for col, val in filters.items():
                q = q.eq(col, val)
        if eq_col and eq_val is not None:
            q = q.eq(eq_col, eq_val)
        if order:
            q = q.order(order)
        if limit:
            q = q.limit(limit)
        res = q.execute()
        return pd.DataFrame(res.data) if res.data else pd.DataFrame()
    except Exception as exc:
        _generic_db_error("SELECT", table)
        if strict:
            raise DatabaseError(f"Impossible de lire la table {table}") from exc
        return pd.DataFrame()


def sb_insert(table: str, data: dict, *, strict: bool = False) -> dict | None:
    try:
        res = get_supabase().table(table).insert(data).execute()
        return res.data[0] if res.data else None
    except Exception as exc:
        _generic_db_error("INSERT", table)
        if strict:
            raise DatabaseError(f"Impossible d'insérer dans {table}") from exc
        st.error("Erreur lors de l'enregistrement.")
        return None


def sb_update(
    table: str,
    data: dict,
    eq_col: str,
    eq_val,
    *,
    strict: bool = False,
) -> bool:
    try:
        get_supabase().table(table).update(data).eq(eq_col, eq_val).execute()
        return True
    except Exception as exc:
        _generic_db_error("UPDATE", table)
        if strict:
            raise DatabaseError(f"Impossible de mettre à jour {table}") from exc
        st.error("Erreur lors de la mise à jour.")
        return False


def sb_delete(table: str, eq_col: str, eq_val, *, strict: bool = False) -> bool:
    try:
        get_supabase().table(table).delete().eq(eq_col, eq_val).execute()
        return True
    except Exception as exc:
        _generic_db_error("DELETE", table)
        if strict:
            raise DatabaseError(f"Impossible de supprimer dans {table}") from exc
        st.error("Erreur lors de la suppression.")
        return False


def sb_rpc(function_name: str, params: dict | None = None, *, strict: bool = False):
    try:
        return get_supabase().rpc(function_name, params or {}).execute().data
    except Exception as exc:
        logger.exception("RPC failed: %s", function_name)
        if strict:
            raise DatabaseError(f"Échec RPC {function_name}") from exc
        return None


def current_authenticated_user_id() -> str | None:
    """Retourne l'identité réellement portée par le JWT Supabase de la session."""
    try:
        res = get_supabase().auth.get_user()
        return str(res.user.id) if res and res.user else None
    except Exception:
        logger.exception("Unable to resolve authenticated Supabase user")
        return None


def audit(action: str, table_name: str, record_id: str | None = None, details: dict | None = None):
    """Écrit un journal d'audit côté serveur via service_role.

    Le SQL de durcissement retire l'écriture directe d'audit_logs aux rôles
    anon/authenticated ; cette fonction reste donc l'unique chemin applicatif.
    """
    try:
        user_id = current_authenticated_user_id() or st.session_state.get("user_id")
        get_supabase_admin().table("audit_logs").insert({
            "structure_id": st.session_state.get("structure_id"),
            "user_id": user_id,
            "action": action,
            "table_name": table_name,
            "record_id": record_id,
            "details": details or {},
        }).execute()
    except Exception:
        logger.exception("audit() failed")
