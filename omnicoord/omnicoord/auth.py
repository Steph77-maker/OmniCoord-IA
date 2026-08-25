"""Authentification Supabase et initialisation de session."""
from __future__ import annotations

import datetime
import logging
import time

import streamlit as st

from .database import audit, get_supabase, reset_user_client
from .security import enregistrer_tentative, est_bloque

logger = logging.getLogger("omnicoord.auth")


def _load_authenticated_profile(user_id: str) -> tuple[dict | None, str]:
    """Charge le profil et la structure avec le JWT/RLS de l'utilisateur."""
    client = get_supabase()
    profile_res = client.table("profils").select("*").eq("id", user_id).single().execute()
    profile = profile_res.data
    if not profile:
        return None, "Non assignée"

    structure_name = "Non assignée"
    if profile.get("structure_id"):
        structure_res = (
            client.table("structures")
            .select("nom")
            .eq("id", profile["structure_id"])
            .single()
            .execute()
        )
        if structure_res.data:
            structure_name = structure_res.data.get("nom", structure_name)
    return profile, structure_name


def check_password() -> bool:
    if st.session_state.get("password_correct", False):
        # Évite un aller-retour Supabase à CHAQUE clic/navigation Streamlit.
        now = time.monotonic()
        last_verified = float(st.session_state.get("_auth_last_verified", 0.0) or 0.0)
        if now - last_verified < 60:
            return True
        try:
            if get_supabase().auth.get_user().user:
                st.session_state["_auth_last_verified"] = now
                return True
        except Exception:
            logger.warning("Session Streamlit présente mais session Supabase invalide")
        for key in ["password_correct", "user_id", "is_admin", "structure_id", "_auth_last_verified"]:
            st.session_state.pop(key, None)

    # Toute l'interface de connexion vit dans un placeholder unique.
    # Au submit, on l'efface AVANT tout appel réseau : ainsi, pendant la
    # vérification Supabase, Streamlit ne peut pas afficher simultanément
    # l'ancien formulaire grisé et un nouveau formulaire actif.
    login_screen = st.empty()
    with login_screen.container():
        st.markdown(
            """
            <div style="text-align:center; margin-top: 60px;">
                <h1 style="color:#f2f5f8;">🩺 OmniCoord IA</h1>
                <p style="color:#8996a3;">Coordination, plannings & sourcing pour l'aide à domicile</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        _, col2, _ = st.columns([1, 1.2, 1])
        with col2:
            with st.form("form_login", clear_on_submit=False):
                email_saisi = st.text_input("Email", key="login_email")
                pwd_saisi = st.text_input("Mot de passe", type="password", key="login_password")
                submit = st.form_submit_button("Se connecter", use_container_width=False)

    if not submit:
        return False

    # Important : effacer le formulaire immédiatement, avant est_bloque() et
    # sign_in_with_password(), qui sont des appels réseau et peuvent prendre
    # quelques secondes sur Streamlit Cloud.
    login_screen.empty()
    progress = st.empty()
    with progress.container():
        _, col2, _ = st.columns([1, 1.2, 1])
        with col2:
            st.info("Connexion en cours…")

    email_saisi = email_saisi.strip().lower()
    if est_bloque(email_saisi):
        progress.empty()
        st.error("⛔ Trop de tentatives. Réessayez plus tard.")
        return False

    try:
        reset_user_client()
        client = get_supabase()
        res = client.auth.sign_in_with_password({
            "email": email_saisi,
            "password": pwd_saisi,
        })
        user = res.user
        if not user:
            enregistrer_tentative(email_saisi, False)
            progress.empty()
            st.error("Email ou mot de passe incorrect.")
            return False

        profil, structure_nom = _load_authenticated_profile(str(user.id))
        if not profil:
            enregistrer_tentative(email_saisi, False)
            client.auth.sign_out()
            progress.empty()
            st.error("Profil introuvable. Contactez l'administrateur.")
            return False

        date_fin_raw = profil.get("date_fin_essai")
        if date_fin_raw and not profil.get("est_admin", False):
            date_fin = datetime.date.fromisoformat(str(date_fin_raw))
            if datetime.date.today() > date_fin:
                enregistrer_tentative(email_saisi, False)
                client.auth.sign_out()
                progress.empty()
                st.error("Votre période d'accès a expiré. Contactez l'administrateur.")
                return False

        enregistrer_tentative(email_saisi, True)
        st.session_state.update({
            "password_correct": True,
            "user_id": str(user.id),
            "user_email": email_saisi,
            "is_admin": bool(profil.get("est_admin", False)),
            "structure_id": profil.get("structure_id"),
            "structure_nom": structure_nom,
            "statut_abonnement": profil.get("statut_abonnement", "ESSAI"),
            "quota_max_ia": profil.get("quota_max_ia", 0),
            "_auth_last_verified": time.monotonic(),
            "mail_config": {
                "email": profil.get("mail_smtp_email", ""),
                "imap": profil.get("mail_imap_server", "imap.gmail.com"),
            },
        })
        audit("LOGIN", "profils", str(user.id))
        progress.empty()
        return True

    except Exception as exc:
        enregistrer_tentative(email_saisi, False)
        progress.empty()
        err_msg = str(exc)
        if "Invalid login" in err_msg or "credentials" in err_msg.lower():
            st.error("Email ou mot de passe incorrect.")
        else:
            logger.exception("Login error")
            st.error("Erreur de connexion. Réessayez.")
        return False
