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



def _set_authenticated_session(user, profil: dict, structure_nom: str, email: str | None = None) -> None:
    """Initialise la session Streamlit après une authentification Supabase valide."""
    user_email = (email or getattr(user, "email", "") or profil.get("email", "")).strip().lower()
    st.session_state.update({
        "password_correct": True,
        "user_id": str(user.id),
        "user_email": user_email,
        "is_admin": bool(profil.get("est_admin", False)),
        "structure_id": profil.get("structure_id"),
        "structure_nom": structure_nom,
        "statut_abonnement": profil.get("statut_abonnement", "ESSAI"),
        "quota_max_ia": profil.get("quota_max_ia", 0),
        "date_fin_essai": profil.get("date_fin_essai"),
        "_auth_last_verified": time.monotonic(),
        "mail_config": {
            "email": profil.get("mail_smtp_email", ""),
            "imap": profil.get("mail_imap_server", "imap.gmail.com"),
        },
    })


def _profile_access_is_valid(profil: dict) -> bool:
    """Applique les règles d'accès commerciales sans faire expirer un abonnement PRO."""
    if profil.get("est_admin", False):
        return True

    statut = str(profil.get("statut_abonnement", "ESSAI") or "ESSAI").strip().upper()
    if statut == "PRO":
        return True
    if statut == "SUSPENDU":
        return False
    if statut != "ESSAI":
        return False

    date_fin_raw = profil.get("date_fin_essai")
    if not date_fin_raw:
        return False
    try:
        date_fin = datetime.date.fromisoformat(str(date_fin_raw))
    except (TypeError, ValueError):
        logger.error("Date de fin d'essai invalide pour le profil %s", profil.get("id"))
        return False
    return datetime.date.today() <= date_fin


def _profile_access_message(profil: dict) -> str:
    """Message utilisateur cohérent avec le statut commercial du compte."""
    statut = str(profil.get("statut_abonnement", "ESSAI") or "ESSAI").strip().upper()
    if statut == "SUSPENDU":
        return "Votre abonnement est suspendu. Contactez l'administrateur."
    if statut == "ESSAI":
        return "Votre période d'essai a expiré. Contactez l'administrateur."
    return "Votre accès OmniCoord n'est pas actif. Contactez l'administrateur."


def _query_param(name: str) -> str:
    value = st.query_params.get(name, "")
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def _process_invitation_link() -> bool:
    """Valide le token d'invitation transmis dans l'URL puis ouvre l'étape mot de passe."""
    token_hash = _query_param("invite_token")
    invite_type = _query_param("type") or "invite"
    if not token_hash:
        return False
    if invite_type != "invite":
        st.error("Lien d'invitation invalide.")
        return False

    st.markdown(
        """
        <div style="text-align:center; margin-top: 60px;">
            <h1 style="color:#f2f5f8;">🩺 OmniCoord IA</h1>
            <p style="color:#8996a3;">Activation de votre accès</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _, col2, _ = st.columns([1, 1.2, 1])
    with col2:
        st.info("Vérification de votre invitation…")

    try:
        reset_user_client()
        client = get_supabase()
        res = client.auth.verify_otp({
            "token_hash": token_hash,
            "type": "invite",
        })
        user = res.user
        session = res.session
        if not user or not session:
            raise RuntimeError("Session d'invitation absente")

        st.session_state.update({
            "_invite_verified": True,
            "_invite_user_id": str(user.id),
            "_invite_email": (getattr(user, "email", "") or "").strip().lower(),
            "_invite_access_token": session.access_token,
            "_invite_refresh_token": session.refresh_token,
        })
        # Le token est à usage unique : on le retire immédiatement de l'URL.
        st.query_params.clear()
        st.rerun()
    except Exception:
        logger.exception("Invitation verification error")
        st.error("Cette invitation est invalide ou a expiré. Demandez une nouvelle invitation à l'administrateur.")
    return False


def _render_invitation_password_setup() -> bool:
    """Permet à l'utilisateur invité de choisir lui-même son premier mot de passe."""
    st.markdown(
        """
        <div style="text-align:center; margin-top: 60px;">
            <h1 style="color:#f2f5f8;">🩺 OmniCoord IA</h1>
            <p style="color:#8996a3;">Finalisez votre compte</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _, col2, _ = st.columns([1, 1.2, 1])
    with col2:
        email = st.session_state.get("_invite_email", "")
        if email:
            st.success(f"Invitation validée pour {email}")
        st.caption("Choisissez maintenant votre mot de passe. Il ne sera jamais communiqué à l'administrateur.")
        with st.form("form_invite_password", clear_on_submit=False):
            p1 = st.text_input("Créer mon mot de passe", type="password")
            p2 = st.text_input("Confirmer le mot de passe", type="password")
            submit = st.form_submit_button("Activer mon compte", use_container_width=True)

        if not submit:
            return False
        if len(p1) < 8:
            st.error("Le mot de passe doit contenir au moins 8 caractères.")
            return False
        if p1 != p2:
            st.error("Les mots de passe ne correspondent pas.")
            return False

        try:
            reset_user_client()
            client = get_supabase()
            client.auth.set_session(
                st.session_state["_invite_access_token"],
                st.session_state["_invite_refresh_token"],
            )
            updated = client.auth.update_user({"password": p1})
            user = updated.user or client.auth.get_user().user
            if not user:
                raise RuntimeError("Utilisateur invité introuvable")

            # Le mot de passe est désormais enregistré. Pour terminer l'activation,
            # on repart volontairement par le même flux de connexion que celui qui
            # fonctionne lors d'une connexion classique. Cela évite de réutiliser
            # la session transitoire issue du token d'invitation pour les lectures RLS.
            invite_email = (st.session_state.get("_invite_email", "") or getattr(user, "email", "") or "").strip().lower()
            if not invite_email:
                raise RuntimeError("Email de l'utilisateur invité introuvable")

            try:
                client.auth.sign_out()
            except Exception:
                logger.warning("Impossible de fermer proprement la session d'invitation")

            reset_user_client()
            login_client = get_supabase()
            login_res = login_client.auth.sign_in_with_password({
                "email": invite_email,
                "password": p1,
            })
            user = login_res.user
            if not user:
                raise RuntimeError("Connexion après activation impossible")

            profil, structure_nom = _load_authenticated_profile(str(user.id))
            if not profil:
                login_client.auth.sign_out()
                st.error("Votre accès existe mais son profil OmniCoord est introuvable. Contactez l'administrateur.")
                return False
            if not _profile_access_is_valid(profil):
                login_client.auth.sign_out()
                st.error(_profile_access_message(profil))
                return False

            _set_authenticated_session(user, profil, structure_nom, invite_email)
            audit("ACCEPT_INVITATION", "profils", str(user.id))
            for key in (
                "_invite_verified", "_invite_user_id", "_invite_email",
                "_invite_access_token", "_invite_refresh_token",
            ):
                st.session_state.pop(key, None)
            st.success("✅ Compte activé. Bienvenue sur OmniCoord IA.")
            st.rerun()
        except Exception:
            logger.exception("Invitation password setup error")
            st.error("Impossible d'activer le compte pour le moment. Réessayez ou demandez une nouvelle invitation.")
    return False

def _mark_login_pending() -> None:
    """Callback du formulaire : marque la tentative avant le rerun Streamlit."""
    st.session_state["_login_pending"] = True


def _render_login_form() -> None:
    """Affiche le formulaire uniquement quand aucune connexion n'est en cours."""
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
            st.text_input("Email", key="login_email")
            st.text_input("Mot de passe", type="password", key="login_password")
            st.form_submit_button(
                "Se connecter",
                use_container_width=False,
                on_click=_mark_login_pending,
            )


def _process_pending_login() -> bool:
    """Traite une tentative déjà soumise sans rerendre le formulaire."""
    email_saisi = str(st.session_state.get("login_email", "")).strip().lower()
    pwd_saisi = str(st.session_state.get("login_password", ""))

    st.session_state["_login_pending"] = False

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
        st.info("Connexion en cours…")

    if not email_saisi or not pwd_saisi:
        st.error("Saisissez votre email et votre mot de passe.")
        return False

    if est_bloque(email_saisi):
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
            st.error("Email ou mot de passe incorrect.")
            return False

        profil, structure_nom = _load_authenticated_profile(str(user.id))
        if not profil:
            client.auth.sign_out()
            st.error("Profil introuvable. Contactez l'administrateur.")
            return False

        if not _profile_access_is_valid(profil):
            client.auth.sign_out()
            st.error(_profile_access_message(profil))
            return False

        enregistrer_tentative(email_saisi, True)
        _set_authenticated_session(user, profil, structure_nom, email_saisi)
        audit("LOGIN", "profils", str(user.id))
        st.session_state.pop("login_password", None)
        st.session_state.pop("_login_pending", None)
        st.rerun()

    except Exception as exc:
        err_msg = str(exc)
        if "Invalid login" in err_msg or "credentials" in err_msg.lower():
            enregistrer_tentative(email_saisi, False)
            st.error("Email ou mot de passe incorrect.")
        else:
            logger.exception("Login error")
            st.error("Erreur de connexion. Réessayez.")
        return False

    return False


def check_password() -> bool:
    # Une invitation commerciale est traitée avant le formulaire de connexion classique.
    if _query_param("invite_token"):
        return _process_invitation_link()
    if st.session_state.get("_invite_verified", False):
        return _render_invitation_password_setup()

    if st.session_state.get("password_correct", False):
        now = time.monotonic()
        last_verified = float(st.session_state.get("_auth_last_verified", 0.0) or 0.0)
        if now - last_verified < 60:
            return True
        try:
            client = get_supabase()
            user = client.auth.get_user().user
            if user:
                profil, structure_nom = _load_authenticated_profile(str(user.id))
                if profil and _profile_access_is_valid(profil):
                    _set_authenticated_session(
                        user,
                        profil,
                        structure_nom,
                        st.session_state.get("user_email"),
                    )
                    return True
                if profil:
                    st.error(_profile_access_message(profil))
                else:
                    st.error("Profil introuvable. Contactez l'administrateur.")
                try:
                    client.auth.sign_out()
                except Exception:
                    logger.warning("Impossible de fermer une session devenue invalide")
        except Exception:
            logger.warning("Session Streamlit présente mais session Supabase invalide")
        for key in [
            "password_correct", "user_id", "user_email", "is_admin", "structure_id",
            "structure_nom", "statut_abonnement", "quota_max_ia", "date_fin_essai",
            "_auth_last_verified",
        ]:
            st.session_state.pop(key, None)

    if st.session_state.get("_login_pending", False):
        return _process_pending_login()

    _render_login_form()
    return False

