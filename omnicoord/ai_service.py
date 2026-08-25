"""Service IA centralisé avec réservation atomique du quota."""
from __future__ import annotations

import json
import logging
import math
from typing import Any

import google.generativeai as genai
import streamlit as st

from .database import get_supabase, get_supabase_admin, current_authenticated_user_id

logger = logging.getLogger("omnicoord.ai")

try:
    gemini_key = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=gemini_key)
    GEMINI_MODEL = st.secrets.get("GEMINI_MODEL", "gemini-2.5-flash")
    model = genai.GenerativeModel(GEMINI_MODEL)
    IA_DISPONIBLE = True
except Exception:
    IA_DISPONIBLE = False
    model = None


def _normalize_quota_payload(data: Any) -> dict:
    # La RPC retourne un jsonb ; selon les versions PostgREST/supabase-py,
    # ``data`` peut être directement un dict ou une liste à un élément.
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return {}


def _reserve_quota() -> tuple[bool, int, int]:
    """Réserve atomiquement une unité de quota côté PostgreSQL.

    La RPC est volontairement réservée au ``service_role``. L'identité
    utilisateur réellement authentifiée est résolue depuis la session Supabase
    courante puis transmise explicitement par le serveur. Ainsi, le client web
    ne peut pas appeler directement la fonction ni modifier son compteur.
    """
    try:
        user_id = current_authenticated_user_id()
        if not user_id:
            return False, 0, 0
        res = get_supabase_admin().rpc(
            "reserve_ai_quota",
            {"p_user_id": user_id},
        ).execute()
        payload = _normalize_quota_payload(res.data)
        clear_quota_cache()
        return (
            bool(payload.get("allowed", False)),
            int(payload.get("used", 0) or 0),
            int(payload.get("quota", 0) or 0),
        )
    except Exception:
        logger.exception("reserve_ai_quota failed")
        return False, 0, 0


@st.cache_data(ttl=30, show_spinner=False)
def _quota_status_cached(user_id: str) -> tuple[bool, int, int]:
    res = (
        get_supabase()
        .table("profils")
        .select("nb_requetes_ia,quota_max_ia,statut_abonnement")
        .eq("id", user_id)
        .single()
        .execute()
    )
    row = res.data or {}
    used = int(row.get("nb_requetes_ia", 0) or 0)
    quota = int(row.get("quota_max_ia", 0) or 0)
    if row.get("statut_abonnement") == "PRO":
        return True, used, quota
    return used < quota, used, quota


def clear_quota_cache() -> None:
    _quota_status_cached.clear()


def peut_utiliser_ia() -> tuple[bool, int, int]:
    """Lecture d'affichage mise en cache ; l'autorisation réelle reste atomique."""
    try:
        user_id = str(st.session_state.get("user_id") or "")
        if not user_id:
            return False, 0, 0
        return _quota_status_cached(user_id)
    except Exception:
        logger.exception("peut_utiliser_ia() failed")
        return False, 0, 0


def incrementer_quota_ia():
    """Compatibilité historique : ne doit plus être appelé par le nouveau code."""
    logger.warning("incrementer_quota_ia() est obsolète ; utiliser la réservation atomique")


def _generate(prompt: str) -> str | None:
    if not IA_DISPONIBLE or model is None:
        st.error("Clé API Gemini non configurée.")
        return None

    allowed, used, quota = _reserve_quota()
    if not allowed:
        st.error(f"Quota IA atteint ({used}/{quota}). Contactez l'administrateur.")
        return None

    try:
        response = model.generate_content(prompt)
        text = (response.text or "").strip()
        if not text:
            raise ValueError("Réponse IA vide")
        st.session_state.pop("_ai_error_shown", None)
        return text
    except Exception:
        logger.exception("Gemini generation failed (model=%s)", globals().get("GEMINI_MODEL", "unknown"))
        # Évite d'afficher la même erreur une fois par candidat lors d'un matching.
        if not st.session_state.get("_ai_error_shown", False):
            st.error("Le service IA est momentanément indisponible. Le classement métier reste disponible sans l’analyse qualitative IA.")
            st.session_state["_ai_error_shown"] = True
        return None


def appel_ia(prompt: str) -> dict | None:
    """Génération JSON. Toutes les réponses JSON passent par ce point d'entrée."""
    text = _generate(prompt)
    if text is None:
        return None
    cleaned = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # La réservation est volontairement conservée : exposer une RPC de
        # "release" à l'utilisateur permettrait de contourner le quota.
        logger.error("IA JSON parse error; response prefix=%r", cleaned[:200])
        st.warning("L'IA a retourné une réponse non structurée. Réessayez.")
        return None


def appel_ia_texte(prompt: str) -> str | None:
    """Génération texte pour transmissions/documents ; pas de contournement JSON."""
    return _generate(prompt)


def distance_km(lat1, lon1, lat2, lon2) -> float | None:
    try:
        radius = 6371
        phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
        dphi = math.radians(float(lat2) - float(lat1))
        dlambda = math.radians(float(lon2) - float(lon1))
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return radius * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))
    except (TypeError, ValueError):
        return None


def classer_candidats_urgence(urg_row: dict, df_dispo):
    """Compatibilité de la V2.

    L'optimisation du matching (préfiltrage/score déterministe/batch habilitations)
    est traitée dans la phase suivante ; cette version bénéficie déjà de la porte
    d'entrée IA et du quota atomique.
    """
    from .database import sb_select

    sid = st.session_state["structure_id"]
    resultats = []
    # Précharge toutes les habilitations en une requête pour supprimer le N+1 DB.
    df_habs_all = sb_select("habilitations", {"structure_id": sid})

    for _, interv in df_dispo.iterrows():
        if df_habs_all.empty:
            df_habs = df_habs_all
        else:
            df_habs = df_habs_all[
                df_habs_all["intervenant_id"].astype(str) == str(interv["id"])
            ]
        habs_txt = "; ".join(
            f"{h['type_habilitation']} (exp. {h['date_expiration']})"
            for _, h in df_habs.iterrows()
        ) or "Aucune habilitation enregistrée"

        prompt = f"""
Tu es un assistant d'aide à la décision pour la coordination de services à domicile.
N'invente aucune donnée et ne prends aucune décision réglementaire à la place du coordinateur.

INTERVENTION :
Date : {urg_row['date_intervention']} de {urg_row['heure_debut']} à {urg_row['heure_fin']}
Type : {urg_row['type_intervention']}
Besoins : {urg_row.get('gestes_techniques', '') or 'Non renseigné'}

CANDIDAT :
Compétences : {interv.get('competences', '')}
Zone : {interv.get('zone_geo', '')}
Disponibilités : {interv.get('disponibilites', '')}
Habilitations enregistrées : {habs_txt}

Réponds UNIQUEMENT en JSON avec :
- score_global (0-100)
- alerte_habilitation (texte court ou "")
- justification (1 phrase)
"""
        data = appel_ia(prompt)
        if data:
            data.update(
                intervenant_id=str(interv["id"]),
                intervenant_nom=f"{interv['prenom']} {interv['nom']}",
                intervenant_email=interv.get("email", ""),
            )
            resultats.append(data)
        else:
            resultats.append({
                "score_global": 0,
                "alerte_habilitation": "",
                "justification": "Évaluation IA indisponible.",
                "intervenant_id": str(interv["id"]),
                "intervenant_nom": f"{interv['prenom']} {interv['nom']}",
                "intervenant_email": interv.get("email", ""),
            })

    return sorted(resultats, key=lambda x: int(x.get("score_global", 0)), reverse=True)
