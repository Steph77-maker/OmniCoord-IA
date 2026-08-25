"""Service d'envoi d'e-mails.

Le secret SMTP n'est jamais conservé en clair dans ``session_state`` : il est
chargé à la demande depuis le profil autorisé par RLS, puis déchiffré uniquement
le temps de l'envoi.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .database import current_authenticated_user_id, get_supabase
from .security import dechiffrer_mdp_mail

logger = logging.getLogger("omnicoord.email")


def _load_mail_credentials() -> tuple[str, str]:
    user_id = current_authenticated_user_id()
    if not user_id:
        return "", ""
    try:
        res = (
            get_supabase()
            .table("profils")
            .select("mail_smtp_email,mail_smtp_password")
            .eq("id", user_id)
            .single()
            .execute()
        )
        row = res.data or {}
        email_from = row.get("mail_smtp_email", "") or ""
        password = dechiffrer_mdp_mail(row.get("mail_smtp_password", "") or "")
        return email_from, password
    except Exception:
        logger.exception("Unable to load mail credentials")
        return "", ""


def envoyer_email(to_email: str, sujet: str, corps: str) -> tuple[bool, str]:
    email_from, password = _load_mail_credentials()
    if not email_from or not password:
        return False, "Boîte mail non configurée dans Mon Profil."
    if not to_email:
        return False, "Email du destinataire manquant."

    try:
        msg = MIMEMultipart()
        msg["From"] = email_from
        msg["To"] = to_email
        msg["Subject"] = sujet
        msg.attach(MIMEText(corps, "plain", "utf-8"))
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.starttls()
            server.login(email_from, password)
            server.sendmail(email_from, to_email, msg.as_string())
        return True, "Email envoyé avec succès."
    except smtplib.SMTPAuthenticationError:
        return False, "Authentification Gmail échouée. Vérifiez le mot de passe d'application."
    except smtplib.SMTPException:
        logger.exception("SMTP error")
        return False, "Erreur SMTP lors de l'envoi."
    except Exception:
        logger.exception("envoyer_email() failed")
        return False, "Erreur d'envoi."
