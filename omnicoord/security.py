"""Sécurité applicative et helpers d'authentification."""
import datetime
import html
import logging
from cryptography.fernet import Fernet
import streamlit as st
from .database import get_supabase_admin

logger = logging.getLogger("omnicoord.security")

_JOURS_FR = {"Monday":"Lundi","Tuesday":"Mardi","Wednesday":"Mercredi","Thursday":"Jeudi","Friday":"Vendredi","Saturday":"Samedi","Sunday":"Dimanche"}
_JOURS_FR_COURT = {"Mon":"Lun","Tue":"Mar","Wed":"Mer","Thu":"Jeu","Fri":"Ven","Sat":"Sam","Sun":"Dim"}
_MOIS_FR = {"January":"janvier","February":"février","March":"mars","April":"avril","May":"mai","June":"juin","July":"juillet","August":"août","September":"septembre","October":"octobre","November":"novembre","December":"décembre"}

def date_fr(d, format_affichage="long"):
    if isinstance(d, str):
        try: d = datetime.date.fromisoformat(d)
        except ValueError: return d
    if format_affichage == "long":
        return f"{_JOURS_FR.get(d.strftime('%A'), d.strftime('%A'))} {d.day} {_MOIS_FR.get(d.strftime('%B'), d.strftime('%B'))} {d.year}"
    if format_affichage == "medium":
        return f"{d.day} {_MOIS_FR.get(d.strftime('%B'), d.strftime('%B'))} {d.year}"
    if format_affichage == "court":
        return d.strftime("%d/%m/%Y")
    if format_affichage == "semaine":
        return f"{_JOURS_FR_COURT.get(d.strftime('%a'), d.strftime('%a'))}. {d.strftime('%d/%m')}"
    return d.strftime("%d/%m/%Y")

def _get_fernet() -> Fernet:
    key = st.secrets.get("FERNET_KEY", "")
    if not key:
        raise ValueError("FERNET_KEY manquante dans les secrets Streamlit.")
    return Fernet(key.encode())

def chiffrer_mdp_mail(mdp_clair: str) -> str:
    if not mdp_clair: return ""
    try: return _get_fernet().encrypt(mdp_clair.encode()).decode()
    except Exception:
        logger.exception("Erreur chiffrement mail")
        return ""

def dechiffrer_mdp_mail(mdp_chiffre: str) -> str:
    if not mdp_chiffre: return ""
    try: return _get_fernet().decrypt(mdp_chiffre.encode()).decode()
    except Exception:
        logger.exception("Erreur déchiffrement mail")
        return ""

def h(valeur) -> str:
    return html.escape(str(valeur or ""))

MAX_ECHECS = 10
FENETRE_MINUTES = 15

def est_bloque(email: str) -> bool:
    """Bloque seulement après MAX_ECHECS échecs consécutifs depuis le dernier succès."""
    try:
        depuis = (datetime.datetime.utcnow() - datetime.timedelta(minutes=FENETRE_MINUTES)).isoformat()
        res = (
            get_supabase_admin()
            .table("login_attempts")
            .select("succes,created_at")
            .eq("email", email)
            .gte("created_at", depuis)
            .order("created_at", desc=True)
            .limit(MAX_ECHECS)
            .execute()
        )

        echecs_consecutifs = 0
        for tentative in (res.data or []):
            if tentative.get("succes") is True:
                break
            echecs_consecutifs += 1

        return echecs_consecutifs >= MAX_ECHECS
    except Exception:
        logger.exception("est_bloque() failed")
        # Une panne du contrôle anti-brute-force ne doit pas créer un faux blocage.
        # Supabase Auth continue de vérifier les identifiants.
        return False

def enregistrer_tentative(email: str, succes: bool):
    try:
        get_supabase_admin().table("login_attempts").insert({"email": email, "succes": succes}).execute()
    except Exception:
        logger.exception("enregistrer_tentative() failed")
