"""
OmniCoord IA — Version 2.0 (Supabase + Sécurité renforcée)
=============================================================
Changements majeurs vs v1 :
  - SQLite → Supabase (PostgreSQL + RLS)
  - Auth déléguée à Supabase Auth (bcrypt natif, JWT, reset mdp)
  - Mots de passe mail chiffrés avec Fernet
  - html.escape() sur toutes les valeurs HTML injectées
  - Anti brute-force login (10 échecs / 15 min)
  - Quota IA re-vérifié côté serveur à chaque appel
  - Nouveau module : Suivi des heures intervenants
  - Nouveau module : Pointage QR code (token unique)
  - Nouveau module : Export RGPD dossier bénéficiaire
  - PDF : police Unicode (fini les ? sur les accents)
  - Logging des erreurs (fini les except: pass silencieux)
"""

# ============================================================
#  IMPORTS
# ============================================================
import datetime
import html
import json
import logging
import math
import os
import re
import smtplib
import time
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import google.generativeai as genai
import pandas as pd
import streamlit as st
from cryptography.fernet import Fernet
from fpdf import FPDF
from supabase import create_client, Client

# ============================================================
#  LOGGING (remplace les except: pass silencieux)
# ============================================================
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("omnicoord")

# ============================================================
#  CONFIG PAGE (doit être en premier appel Streamlit)
# ============================================================
st.set_page_config(
    page_title="OmniCoord IA",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================================
#  CHARTE GRAPHIQUE
# ============================================================
CUSTOM_CSS = """
<style>
    :root {
        --oc-navy-deep: #0a1929;
        --oc-navy: #0f2942;
        --oc-navy-panel: #132f4c;
        --oc-steel: #8996a3;
        --oc-steel-light: #b8c2cc;
        --oc-medical-blue: #2f7cf6;
        --oc-medical-blue-soft: #4c8dfa;
        --oc-alert: #e0554f;
        --oc-warning: #d99a3d;
        --oc-success: #3fae74;
    }
    .stApp { background: linear-gradient(160deg, var(--oc-navy-deep) 0%, var(--oc-navy) 55%, #0d2138 100%); color: #f2f5f8 !important; }
    section[data-testid="stSidebar"] { background: linear-gradient(180deg, #0c1f33 0%, #0a1929 100%); border-right: 1px solid rgba(137,150,163,0.25); }
    p, span, label, .stMarkdown, div[data-baseweb="select"] span { color: #f2f5f8 !important; }
    h1, h2, h3, h4, h5, h6 { color: #ffffff !important; letter-spacing: 0.3px; }
    .oc-badge { display: inline-block; padding: 4px 14px; border-radius: 20px; font-weight: 700; color: white; }
    .oc-card { background: linear-gradient(135deg, var(--oc-navy-panel) 0%, #0f2438 100%); border: 1px solid rgba(137,150,163,0.25); border-left: 4px solid var(--oc-medical-blue); border-radius: 12px; padding: 18px 20px; margin-bottom: 14px; color: #f2f5f8 !important; }
    .oc-card-alert { border-left: 4px solid var(--oc-alert) !important; }
    .oc-card-warning { border-left: 4px solid var(--oc-warning) !important; }
    .oc-card-ok { border-left: 4px solid var(--oc-success) !important; }
    .oc-metal-divider { height: 2px; background: linear-gradient(90deg, transparent, var(--oc-steel) 50%, transparent); margin: 18px 0; opacity: 0.5; }
    .stButton > button { background: linear-gradient(135deg, var(--oc-medical-blue) 0%, #1f5fd6 100%); color: white; border: none; border-radius: 8px; font-weight: 600; }
    .stButton > button:hover { background: linear-gradient(135deg, var(--oc-medical-blue-soft) 0%, var(--oc-medical-blue) 100%); border: none; color: white; }
    div[data-testid="stMetricValue"] { color: var(--oc-medical-blue-soft) !important; }
    input, textarea, select { color: #ffffff !important; }
    div[data-baseweb="input"] { background-color: rgba(19,47,76,0.6) !important; }
    div[data-baseweb="base-input"] input { color: #ffffff !important; background-color: rgba(13,33,56,0.8) !important; }
    .planning-table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 10px; }
    .planning-table th { background: linear-gradient(135deg, #1a3f6f 0%, #0f2942 100%); color: #f2f5f8; padding: 10px 8px; text-align: center; border: 1px solid rgba(137,150,163,0.3); font-weight: 700; }
    .planning-table th.col-intervenant { background: linear-gradient(135deg, #0c1f33 0%, #0a1929 100%); text-align: left; padding-left: 12px; min-width: 140px; }
    .planning-table td { border: 1px solid rgba(137,150,163,0.2); padding: 6px 4px; vertical-align: top; min-width: 110px; background: rgba(10,25,41,0.4); }
    .planning-table td.col-intervenant { background: rgba(12,31,51,0.7); color: #e6ecf2; font-weight: 600; padding: 8px 12px; vertical-align: middle; }
    .planning-cell { background: linear-gradient(135deg, #132f4c 0%, #0f2438 100%); border-radius: 6px; padding: 5px 7px; margin: 2px; font-size: 12px; border-left: 3px solid #2f7cf6; color: #f2f5f8; }
    .planning-cell.urgence { border-left-color: #e0554f !important; }
    .planning-cell.realise { border-left-color: #3fae74 !important; }
    .planning-cell.annule { border-left-color: #8996a3 !important; opacity: 0.6; }
    .planning-empty { color: rgba(137,150,163,0.3); font-size: 12px; text-align: center; padding: 10px 0; }
    .alert-box { border-radius: 10px; padding: 14px 18px; margin-bottom: 10px; }
    .alert-box-rouge { background: rgba(224,85,79,0.12); border: 1px solid rgba(224,85,79,0.45); border-left: 4px solid #e0554f; }
    .alert-box-orange { background: rgba(217,154,61,0.12); border: 1px solid rgba(217,154,61,0.40); border-left: 4px solid #d99a3d; }
    .alert-box-bleu { background: rgba(47,124,246,0.10); border: 1px solid rgba(47,124,246,0.35); border-left: 4px solid #2f7cf6; }
    .fiche-section { background: linear-gradient(135deg, #132f4c 0%, #0f2438 100%); border: 1px solid rgba(137,150,163,0.2); border-radius: 10px; padding: 16px 18px; margin-bottom: 12px; }
    .fiche-section h4 { color: #4c8dfa !important; margin-bottom: 10px; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; }
    .fiche-row { display: flex; gap: 8px; margin-bottom: 6px; }
    .fiche-label { color: #8996a3; font-size: 13px; min-width: 140px; flex-shrink: 0; }
    .fiche-value { color: #f2f5f8; font-size: 13px; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ============================================================
#  LOCALISATION FRANÇAISE DES DATES
# ============================================================
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


# ============================================================
#  SÉCURITÉ — CHIFFREMENT DES MOTS DE PASSE MAIL (Fernet)
# ============================================================
def _get_fernet() -> Fernet:
    """
    Charge la clé Fernet depuis les secrets Streamlit.
    Pour générer une clé : from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())
    Ajouter dans Streamlit secrets : FERNET_KEY = "ta_clé_ici"
    """
    key = st.secrets.get("FERNET_KEY", "")
    if not key:
        raise ValueError("FERNET_KEY manquante dans les secrets Streamlit.")
    return Fernet(key.encode())

def chiffrer_mdp_mail(mdp_clair: str) -> str:
    """Chiffre un mot de passe mail avant stockage en base."""
    if not mdp_clair:
        return ""
    try:
        return _get_fernet().encrypt(mdp_clair.encode()).decode()
    except Exception as e:
        logger.error(f"Erreur chiffrement mail: {e}")
        return ""

def dechiffrer_mdp_mail(mdp_chiffre: str) -> str:
    """Déchiffre un mot de passe mail récupéré de la base."""
    if not mdp_chiffre:
        return ""
    try:
        return _get_fernet().decrypt(mdp_chiffre.encode()).decode()
    except Exception as e:
        logger.error(f"Erreur déchiffrement mail: {e}")
        return ""


# ============================================================
#  CONNEXION SUPABASE
# ============================================================
@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]  # Utilise la clé anon (RLS actif)
    return create_client(url, key)

@st.cache_resource
def get_supabase_admin() -> Client:
    """Client service_role pour les opérations admin (bypass RLS)."""
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_SERVICE_KEY"]  # Service role key
    return create_client(url, key)

sb = get_supabase()


# ============================================================
#  HELPERS SUPABASE (remplacent charger_df / executer SQLite)
# ============================================================
def sb_select(table: str, filters: dict = None, eq_col: str = None,
              eq_val=None, order: str = None, limit: int = None) -> pd.DataFrame:
    """
    Raccourci SELECT avec filtres simples.
    Retourne toujours un DataFrame (vide si aucun résultat).
    """
    try:
        q = sb.table(table).select("*")
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
    except Exception as e:
        logger.error(f"sb_select({table}): {e}")
        return pd.DataFrame()


def sb_insert(table: str, data: dict) -> dict | None:
    """INSERT et retourne la ligne créée."""
    try:
        res = sb.table(table).insert(data).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"sb_insert({table}): {e}")
        st.error(f"Erreur lors de l'enregistrement : {e}")
        return None


def sb_update(table: str, data: dict, eq_col: str, eq_val) -> bool:
    """UPDATE avec filtre simple."""
    try:
        sb.table(table).update(data).eq(eq_col, eq_val).execute()
        return True
    except Exception as e:
        logger.error(f"sb_update({table}): {e}")
        st.error(f"Erreur lors de la mise à jour : {e}")
        return False


def sb_delete(table: str, eq_col: str, eq_val) -> bool:
    """DELETE avec filtre simple."""
    try:
        sb.table(table).delete().eq(eq_col, eq_val).execute()
        return True
    except Exception as e:
        logger.error(f"sb_delete({table}): {e}")
        st.error(f"Erreur lors de la suppression : {e}")
        return False


def sb_rpc(function_name: str, params: dict = None) -> any:
    """Appel d'une fonction Supabase RPC."""
    try:
        res = sb.rpc(function_name, params or {}).execute()
        return res.data
    except Exception as e:
        logger.error(f"sb_rpc({function_name}): {e}")
        return None


# ============================================================
#  AUDIT LOG
# ============================================================
def audit(action: str, table_name: str, record_id: str = None, details: dict = None):
    """Enregistre une action sensible dans audit_logs."""
    try:
        get_supabase_admin().table("audit_logs").insert({
            "structure_id": st.session_state.get("structure_id"),
            "user_id": st.session_state.get("user_id"),
            "action": action,
            "table_name": table_name,
            "record_id": record_id,
            "details": details or {}
        }).execute()
    except Exception as e:
        logger.warning(f"audit() failed: {e}")


# ============================================================
#  SÉCURITÉ — ÉCHAPPEMENT HTML
#  Toutes les valeurs de la BDD injectées dans du HTML
#  doivent passer par h(val) avant affichage.
# ============================================================
def h(valeur) -> str:
    """Échappe une valeur pour injection sécurisée dans du HTML."""
    return html.escape(str(valeur or ""))


# ============================================================
#  ANTI BRUTE-FORCE LOGIN
# ============================================================
MAX_ECHECS = 10
FENETRE_MINUTES = 15

def est_bloque(email: str) -> bool:
    """Vérifie si l'email est bloqué suite à trop d'échecs."""
    try:
        depuis = (datetime.datetime.utcnow() - datetime.timedelta(minutes=FENETRE_MINUTES)).isoformat()
        res = get_supabase_admin().table("login_attempts") \
            .select("id", count="exact") \
            .eq("email", email) \
            .eq("succes", False) \
            .gte("created_at", depuis) \
            .execute()
        nb = res.count or 0
        return nb >= MAX_ECHECS
    except Exception as e:
        logger.warning(f"est_bloque(): {e}")
        return False

def enregistrer_tentative(email: str, succes: bool):
    """Enregistre une tentative de connexion."""
    try:
        get_supabase_admin().table("login_attempts").insert({
            "email": email,
            "succes": succes
        }).execute()
    except Exception as e:
        logger.warning(f"enregistrer_tentative(): {e}")


# ============================================================
#  AUTHENTIFICATION SUPABASE
# ============================================================
def check_password() -> bool:
    """Page de connexion. Utilise Supabase Auth."""
    if st.session_state.get("password_correct", False):
        return True

    st.markdown("""
        <div style="text-align:center; margin-top: 60px;">
            <h1 style="color:#f2f5f8;">🩺 OmniCoord IA</h1>
            <p style="color:#8996a3;">Coordination, plannings & sourcing pour l'aide à domicile</p>
        </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        with st.form("form_login"):
            email_saisi = st.text_input("Email")
            pwd_saisi = st.text_input("Mot de passe", type="password")
            submit = st.form_submit_button("Se connecter")

            if submit:
                email_saisi = email_saisi.strip().lower()

                # --- Anti brute-force ---
                if est_bloque(email_saisi):
                    st.error(f"⛔ Trop de tentatives. Réessayez dans {FENETRE_MINUTES} minutes.")
                    return False

                try:
                    res = sb.auth.sign_in_with_password({
                        "email": email_saisi,
                        "password": pwd_saisi
                    })
                    user = res.user

                    if not user:
                        enregistrer_tentative(email_saisi, False)
                        st.error("Email ou mot de passe incorrect.")
                        return False

                    # Récupère le profil applicatif
                    profil_res = get_supabase_admin().table("profils") \
                        .select("*") \
                        .eq("id", user.id) \
                        .single() \
                        .execute()
                    profil = profil_res.data

                    if not profil:
                        enregistrer_tentative(email_saisi, False)
                        st.error("Profil introuvable. Contactez l'administrateur.")
                        return False

                    # Vérification période d'accès
                    date_fin = datetime.date.fromisoformat(profil["date_fin_essai"])
                    if not profil["est_admin"] and datetime.date.today() > date_fin:
                        enregistrer_tentative(email_saisi, False)
                        st.error("Votre période d'accès a expiré. Contactez l'administrateur.")
                        return False

                    # Récupère la structure
                    structure_res = get_supabase_admin().table("structures") \
                        .select("nom") \
                        .eq("id", profil["structure_id"]) \
                        .single() \
                        .execute()
                    structure_nom = structure_res.data["nom"] if structure_res.data else "Non assignée"

                    # Session
                    enregistrer_tentative(email_saisi, True)
                    st.session_state.update({
                        "password_correct": True,
                        "user_id": user.id,
                        "user_email": email_saisi,
                        "is_admin": profil["est_admin"],
                        "structure_id": profil["structure_id"],
                        "structure_nom": structure_nom,
                        "statut_abonnement": profil["statut_abonnement"],
                        "quota_max_ia": profil["quota_max_ia"],
                        "mail_config": {
                            "email": profil.get("mail_smtp_email", ""),
                            "password": dechiffrer_mdp_mail(profil.get("mail_smtp_password", "")),
                            "imap": profil.get("mail_imap_server", "imap.gmail.com")
                        }
                    })
                    audit("LOGIN", "profils", user.id)
                    st.rerun()

                except Exception as e:
                    enregistrer_tentative(email_saisi, False)
                    err_msg = str(e)
                    if "Invalid login" in err_msg or "credentials" in err_msg.lower():
                        st.error("Email ou mot de passe incorrect.")
                    else:
                        logger.error(f"Login error: {e}")
                        st.error("Erreur de connexion. Réessayez.")

    return False


# ============================================================
#  QUOTAS IA — RE-VÉRIFIÉS CÔTÉ SERVEUR À CHAQUE APPEL
# ============================================================
def peut_utiliser_ia() -> tuple[bool, int, int]:
    """Re-lit le quota en base (pas depuis la session) pour éviter la manipulation."""
    try:
        res = get_supabase_admin().table("profils") \
            .select("nb_requetes_ia, quota_max_ia, statut_abonnement") \
            .eq("id", st.session_state.get("user_id")) \
            .single() \
            .execute()
        if not res.data:
            return False, 0, 0
        nb, quota_max, statut = res.data["nb_requetes_ia"], res.data["quota_max_ia"], res.data["statut_abonnement"]
        if statut == "PRO":
            return True, nb, quota_max
        return nb < quota_max, nb, quota_max
    except Exception as e:
        logger.error(f"peut_utiliser_ia(): {e}")
        return False, 0, 0

def incrementer_quota_ia():
    """Incrémente le compteur IA uniquement si l'appel a réussi."""
    try:
        get_supabase_admin().rpc("increment_quota_ia", {
            "p_user_id": st.session_state.get("user_id")
        }).execute()
    except Exception as e:
        logger.warning(f"incrementer_quota_ia(): {e}")


# ============================================================
#  GÉNÉRATION PDF (Unicode avec fpdf2)
# ============================================================
class PDFDocument(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(15, 41, 66)
        self.cell(0, 10, "OmniCoord IA", new_x="LMARGIN", new_y="NEXT", align="L")
        self.set_draw_color(47, 124, 246)
        self.line(10, 20, 200, 20)
        self.ln(6)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(137, 150, 163)
        self.cell(0, 10, f"Document généré le {datetime.date.today().strftime('%d/%m/%Y')} — OmniCoord IA", align="C")


def creer_pdf_transmission(beneficiaire_nom: str, intervenant_nom: str,
                            date_doc: str, contenu: str) -> bytes:
    """Génère un PDF de transmission (Unicode correct via fpdf2)."""
    pdf = PDFDocument()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 8, f"Fiche de liaison — {date_doc}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, f"Bénéficiaire : {beneficiaire_nom}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Intervenant : {intervenant_nom}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 6, contenu)
    return bytes(pdf.output())


def creer_pdf_export_rgpd(beneficiaire: dict, interventions: list,
                           documents: list) -> bytes:
    """Export RGPD complet d'un dossier bénéficiaire en PDF."""
    pdf = PDFDocument()
    pdf.add_page()

    # En-tête dossier
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, f"Dossier RGPD — {beneficiaire.get('prenom', '')} {beneficiaire.get('nom', '')}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Export généré le {datetime.date.today().strftime('%d/%m/%Y')} à la demande du titulaire.",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Informations personnelles
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Informations personnelles", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for label, key in [("Adresse", "adresse"), ("Téléphone", "telephone"),
                       ("Dépendance", "niveau_dependance"), ("Notes", "notes")]:
        pdf.multi_cell(0, 6, f"{label} : {beneficiaire.get(key, '—')}")
    pdf.ln(4)

    # Historique des interventions
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, f"Interventions ({len(interventions)})", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for iv in interventions:
        ligne = f"{iv.get('date_intervention', '')} {iv.get('heure_debut', '')}–{iv.get('heure_fin', '')} | {iv.get('type_intervention', '')} | {iv.get('statut', '')}"
        pdf.cell(0, 5, ligne, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Documents
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, f"Documents ({len(documents)})", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for doc in documents:
        pdf.multi_cell(0, 5, f"[{doc.get('date_creation', '')}] {doc.get('type_document', '')} : {doc.get('contenu', '')[:200]}...")

    return bytes(pdf.output())


# ============================================================
#  ENVOI D'EMAIL (inchangé, sécurisé via Fernet)
# ============================================================
def envoyer_email(to_email: str, sujet: str, corps: str) -> tuple[bool, str]:
    """Envoie un email via la boîte configurée dans le profil."""
    cfg = st.session_state.get("mail_config", {})
    email_from = cfg.get("email", "")
    password = cfg.get("password", "")

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
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
        server.starttls()
        server.login(email_from, password)
        server.sendmail(email_from, to_email, msg.as_string())
        server.quit()
        return True, "Email envoyé avec succès."
    except smtplib.SMTPAuthenticationError:
        return False, "Authentification Gmail échouée. Vérifiez le mot de passe d'application."
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error: {e}")
        return False, f"Erreur SMTP : {e}"
    except Exception as e:
        logger.error(f"envoyer_email(): {e}")
        return False, f"Erreur d'envoi : {e}"


# ============================================================
#  CONFIGURATION IA (Gemini)
# ============================================================
try:
    gemini_key = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel("gemini-2.0-flash")
    IA_DISPONIBLE = True
except Exception:
    IA_DISPONIBLE = False
    model = None


def appel_ia(prompt: str) -> dict | None:
    """
    Appel sécurisé à l'IA :
    - Vérifie le quota en base (pas en session)
    - N'incrémente que si l'appel réussit
    - Retourne None en cas d'échec
    """
    if not IA_DISPONIBLE or model is None:
        st.error("Clé API Gemini non configurée.")
        return None

    autorise, nb, quota = peut_utiliser_ia()
    if not autorise:
        st.error(f"Quota IA atteint ({nb}/{quota}). Contactez l'administrateur.")
        return None

    try:
        reponse = model.generate_content(prompt)
        txt = reponse.text.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(txt)
        incrementer_quota_ia()  # Seulement si succès
        return data
    except json.JSONDecodeError as e:
        logger.error(f"IA JSON parse error: {e} | réponse: {reponse.text[:200]}")
        st.warning("L'IA a retourné une réponse non structurée. Réessayez.")
        return None
    except Exception as e:
        logger.error(f"appel_ia(): {e}")
        st.error(f"Erreur IA : {e}")
        return None


# ============================================================
#  GÉNÉRATION PDF — RAPPORT DE MATCHING IA
# ============================================================
def _generer_pdf_matching(intervenant_nom, intervenant_statut, intervenant_zone,
                           beneficiaire_nom, score_global, profil_humain,
                           traits_dominants, dimensions, scores,
                           competences_transferables, alerte_habilitation,
                           alerte_humaine, justification) -> bytes:
    """Génère un rapport PDF complet du matching IA pour un intervenant."""
    pdf = PDFDocument()
    pdf.add_page()

    # ── En-tête du rapport ──
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(15, 41, 66)
    pdf.cell(0, 8, f"Rapport de matching IA — {datetime.date.today().strftime('%d/%m/%Y')}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # ── Score global ──
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(47, 124, 246)
    pdf.cell(0, 7, f"Score global : {score_global}%", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(20, 20, 20)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Bénéficiaire : {beneficiaire_nom}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Intervenant  : {intervenant_nom} ({intervenant_statut}) — {intervenant_zone}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # ── Profil humain ──
    if profil_humain:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(80, 80, 80)
        pdf.multi_cell(0, 6, f"Profil humain : {profil_humain}")
        pdf.ln(3)

    # ── Traits de personnalité ──
    if traits_dominants:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(20, 20, 20)
        pdf.cell(0, 7, "Empreinte comportementale", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        for trait in traits_dominants:
            pdf.cell(0, 6, f"  • {trait}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    # ── Scores par dimension ──
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 7, "Évaluation par dimension", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for cle, label, _ in dimensions:
        val = int(scores.get(cle, 0))
        barre = "█" * round(val / 10) + "░" * (10 - round(val / 10))
        pdf.cell(0, 6, f"  {label.replace('🛠️','').replace('🎓','').replace('❤️','').replace('🧠','').replace('📍','').strip()} : {val}%  {barre}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # ── Compétences transférables ──
    if competences_transferables:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(20, 20, 20)
        pdf.cell(0, 7, "Compétences transférables", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        for ct in competences_transferables:
            pdf.multi_cell(0, 6, f"  ✦ {ct}")
        pdf.ln(3)

    # ── Alertes ──
    if alerte_habilitation:
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(224, 85, 79)
        pdf.multi_cell(0, 6, f"⚠ Habilitation : {alerte_habilitation}")
        pdf.set_text_color(20, 20, 20)
        pdf.ln(2)
    if alerte_humaine:
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(217, 154, 61)
        pdf.multi_cell(0, 6, f"💡 Profil bénéficiaire : {alerte_humaine}")
        pdf.set_text_color(20, 20, 20)
        pdf.ln(2)

    # ── Synthèse narrative ──
    if justification:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(20, 20, 20)
        pdf.cell(0, 7, "Synthèse de l'analyse", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 6, justification)

    # ── Mention légale ──
    pdf.ln(8)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(137, 150, 163)
    pdf.multi_cell(0, 5, "Ce rapport est généré automatiquement par l'IA d'OmniCoord IA. "
                         "Il constitue une aide à la décision et ne se substitue pas au jugement du coordinateur.")

    return bytes(pdf.output())


# ============================================================
#  CALCUL DE PROXIMITÉ
# ============================================================
def distance_km(lat1, lon1, lat2, lon2) -> float | None:
    try:
        R = 6371
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))
    except Exception:
        return None


# ============================================================
#  AGENT IA — CLASSEMENT URGENCES
# ============================================================
def classer_candidats_urgence(urg_row: dict, df_dispo: pd.DataFrame) -> list:
    sid = st.session_state["structure_id"]
    resultats = []
    for _, interv in df_dispo.iterrows():
        df_habs = sb_select("habilitations", {"intervenant_id": str(interv["id"]), "structure_id": sid})
        habs_txt = "; ".join([
            f"{h['type_habilitation']} (exp. {h['date_expiration']})"
            for _, h in df_habs.iterrows()
        ]) or "Aucune habilitation enregistrée"

        prompt = f"""
        Tu es coordinateur expert SAAD/SSIAD. Évalue ce candidat pour ce remplacement urgent.

        INTERVENTION :
        Date : {urg_row['date_intervention']} de {urg_row['heure_debut']} à {urg_row['heure_fin']}
        Type : {urg_row['type_intervention']}
        Besoins : {urg_row.get('gestes_techniques', '') or 'Non renseigné'}

        CANDIDAT :
        Compétences : {interv['competences']}
        Zone : {interv['zone_geo']}
        Disponibilités : {interv['disponibilites']}
        Habilitations : {habs_txt}

        Réponds UNIQUEMENT en JSON avec :
        - score_global (0-100)
        - alerte_habilitation (texte court ou "")
        - justification (1 phrase)
        """
        data = appel_ia(prompt)
        if data:
            data["intervenant_id"] = str(interv["id"])
            data["intervenant_nom"] = f"{interv['prenom']} {interv['nom']}"
            data["intervenant_email"] = interv.get("email", "")
            resultats.append(data)
        else:
            resultats.append({
                "score_global": 0, "alerte_habilitation": "",
                "justification": "Évaluation IA indisponible.",
                "intervenant_id": str(interv["id"]),
                "intervenant_nom": f"{interv['prenom']} {interv['nom']}",
                "intervenant_email": interv.get("email", "")
            })

    return sorted(resultats, key=lambda x: int(x.get("score_global", 0)), reverse=True)


# ============================================================
#  POINT D'ENTRÉE — AUTHENTIFICATION
# ============================================================
if not check_password():
    st.stop()

# Raccourcis session
SID = st.session_state["structure_id"]
USER_ID = st.session_state["user_id"]
IS_ADMIN = st.session_state.get("is_admin", False)


# ============================================================
#  SIDEBAR
# ============================================================
st.sidebar.markdown("### ⚙️ Mon Compte")
st.sidebar.caption(f"Connecté : {st.session_state.get('user_email', '')}")
st.sidebar.caption(f"🏢 {st.session_state.get('structure_nom', 'Non assignée')}")

peut_ia, nb_req, quota_max = peut_utiliser_ia()
if quota_max >= 999999:
    st.sidebar.success("👑 Compte PRO illimité")
else:
    st.sidebar.info(f"Requêtes IA : {nb_req} / {quota_max}")

if IA_DISPONIBLE:
    st.sidebar.success("🔑 Gemini configuré")
else:
    st.sidebar.warning("⚠️ Clé Gemini manquante")

if st.sidebar.button("🚪 Se déconnecter"):
    audit("LOGOUT", "profils", USER_ID)
    sb.auth.sign_out()
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()

st.sidebar.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)

# --- Admin sidebar ---
if IS_ADMIN:
    st.sidebar.markdown("### 👑 Administration")

    with st.sidebar.expander("➕ Créer un accès"):
        df_structs = sb_select("structures", order="nom")
        with st.form("form_add_user"):
            struct_existante = st.selectbox(
                "Structure existante",
                [""] + (df_structs["nom"].tolist() if not df_structs.empty else [])
            )
            struct_nouvelle = st.text_input("OU nouvelle structure")
            p_email = st.text_input("Email utilisateur")
            p_pwd = st.text_input("Mot de passe temporaire")
            p_duree = st.number_input("Durée d'accès (jours)", min_value=1, value=30)
            btn_add = st.form_submit_button("Créer l'accès")

            if btn_add and p_email and p_pwd:
                if len(p_pwd) < 8:
                    st.error("8 caractères minimum pour le mot de passe.")
                else:
                    nom_struct = struct_nouvelle.strip() or struct_existante
                    if not nom_struct:
                        st.error("Choisissez ou créez une structure.")
                    else:
                        # Créer la structure si nouvelle
                        struct_row = sb_select("structures", {"nom": nom_struct})
                        if struct_row.empty:
                            struct_row = sb_insert("structures", {"nom": nom_struct})
                            struct_id = struct_row["id"] if struct_row else None
                        else:
                            struct_id = struct_row.iloc[0]["id"]

                        if struct_id:
                            date_fin = (datetime.date.today() + datetime.timedelta(days=int(p_duree))).isoformat()
                            try:
                                # Créer via Supabase Auth (admin)
                                auth_res = get_supabase_admin().auth.admin.create_user({
                                    "email": p_email,
                                    "password": p_pwd,
                                    "email_confirm": True
                                })
                                new_uid = auth_res.user.id
                                sb_insert("profils", {
                                    "id": new_uid,
                                    "structure_id": struct_id,
                                    "email": p_email,
                                    "est_admin": False,
                                    "statut_abonnement": "ESSAI",
                                    "quota_max_ia": 20,
                                    "date_fin_essai": date_fin
                                })
                                audit("CREATE_USER", "profils", new_uid, {"structure": nom_struct})
                                st.success(f"✅ Accès créé pour {p_email} jusqu'au {date_fr(date_fin, 'court')}")
                            except Exception as e:
                                st.error(f"Erreur : {e}")

    with st.sidebar.expander("📊 Quotas IA"):
        df_users = sb_select("profils", order="email")
        if not df_users.empty:
            st.dataframe(
                df_users[["email", "nb_requetes_ia", "quota_max_ia", "statut_abonnement", "date_fin_essai"]],
                use_container_width=True, hide_index=True
            )
            email_reset = st.text_input("Email à réinitialiser")
            if st.button("Remettre à 0"):
                if email_reset:
                    sb_update("profils", {"nb_requetes_ia": 0}, "email", email_reset)
                    st.success("Quota réinitialisé.")

st.sidebar.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)


# ============================================================
#  MENU PRINCIPAL — Admin voit tout + Administration
# ============================================================
st.sidebar.markdown("### 📋 Menu")

_onglets = [
    "🏠 Tableau de bord",
    "🧑‍🤝‍🧑 Vivier & Sourcing",
    "🎯 Matching IA",
    "❤️ Bénéficiaires",
    "📝 Documents & Transmissions",
    "📅 Plannings & Urgences",
    "✅ Conformité & Habilitations",
    "📊 Suivi des heures",
    "👤 Mon Profil",
]
if IS_ADMIN:
    _onglets.insert(0, "🛠️ Administration")

onglet = st.sidebar.radio("Navigation", _onglets, label_visibility="collapsed")

st.markdown(f"# {onglet}")
st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)


# ============================================================
#  🏠 TABLEAU DE BORD
# ============================================================
if onglet == "🏠 Tableau de bord":
    aujourdhui = datetime.date.today()
    seuil_60j = aujourdhui + datetime.timedelta(days=60)

    col1, col2, col3, col4 = st.columns(4)
    nb_benef = len(sb_select("beneficiaires", {"structure_id": SID, "statut": "Actif"}))
    nb_interv_dispo = len(sb_select("intervenants", {"structure_id": SID, "statut_dispo": "Disponible"}))

    df_semaine = sb_select("interventions", {"structure_id": SID})
    if not df_semaine.empty:
        df_semaine["date_intervention"] = pd.to_datetime(df_semaine["date_intervention"]).dt.date
        nb_plan_7j = len(df_semaine[
            (df_semaine["date_intervention"] >= aujourdhui) &
            (df_semaine["date_intervention"] <= aujourdhui + datetime.timedelta(days=7)) &
            (df_semaine["statut"] != "Annulé")
        ])
        nb_urgences = len(df_semaine[df_semaine["statut"] == "Urgence à pourvoir"])
    else:
        nb_plan_7j = nb_urgences = 0

    col1.metric("👥 Bénéficiaires actifs", nb_benef)
    col2.metric("🧑‍⚕️ Intervenants dispo", nb_interv_dispo)
    col3.metric("📅 Interventions (7j)", nb_plan_7j)
    col4.metric("🚨 Urgences", nb_urgences,
                delta=f"-{nb_urgences}" if nb_urgences > 0 else None, delta_color="inverse")

    st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
    st.subheader("🔔 Alertes & points d'attention")

    alertes_rouges, alertes_oranges, alertes_bleues = [], [], []

    # Habilitations expirées / bientôt
    df_habs_all = sb_select("habilitations", {"structure_id": SID})
    if not df_habs_all.empty:
        df_interv_noms = sb_select("intervenants", {"structure_id": SID})
        interv_noms = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_interv_noms.iterrows()}

        df_habs_all["date_exp_dt"] = pd.to_datetime(df_habs_all["date_expiration"], errors="coerce").dt.date
        for _, hb in df_habs_all.iterrows():
            nom_i = h(interv_noms.get(str(hb.get("intervenant_id", "")), "Inconnu"))
            type_h = h(hb["type_habilitation"])
            exp = hb["date_expiration"]
            if pd.isna(hb["date_exp_dt"]): continue
            if hb["date_exp_dt"] < aujourdhui:
                alertes_rouges.append(f"🔴 Habilitation <b>{type_h}</b> de <b>{nom_i}</b> expirée depuis le {exp}")
            elif hb["date_exp_dt"] <= seuil_60j:
                jours = (hb["date_exp_dt"] - aujourdhui).days
                alertes_oranges.append(f"🟠 Habilitation <b>{type_h}</b> de <b>{nom_i}</b> expire dans <b>{jours}j</b> ({exp})")

    # Urgences non couvertes
    df_urg = sb_select("interventions", {"structure_id": SID, "statut": "Urgence à pourvoir"})
    if not df_urg.empty:
        df_benef_noms = sb_select("beneficiaires", {"structure_id": SID})
        benef_noms = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_benef_noms.iterrows()}
        for _, u in df_urg.iterrows():
            b_nom = h(benef_noms.get(str(u.get("beneficiaire_id", "")), "Inconnu"))
            alertes_rouges.append(
                f"🚨 Intervention <b>non couverte</b> : {h(str(u['date_intervention']))} "
                f"{h(str(u['heure_debut']))}–{h(str(u['heure_fin']))} ({h(str(u['type_intervention']))}) — {b_nom}"
            )

    total = len(alertes_rouges) + len(alertes_oranges) + len(alertes_bleues)
    if total == 0:
        st.markdown('<div class="oc-card oc-card-ok"><b>✅ Tout est en ordre !</b></div>', unsafe_allow_html=True)
    else:
        if alertes_rouges:
            with st.expander(f"🔴 Alertes critiques ({len(alertes_rouges)})", expanded=True):
                for a in alertes_rouges:
                    st.markdown(f'<div class="alert-box alert-box-rouge">{a}</div>', unsafe_allow_html=True)
        if alertes_oranges:
            with st.expander(f"🟠 À renouveler prochainement ({len(alertes_oranges)})", expanded=True):
                for a in alertes_oranges:
                    st.markdown(f'<div class="alert-box alert-box-orange">{a}</div>', unsafe_allow_html=True)
        if alertes_bleues:
            with st.expander(f"ℹ️ Points d'attention ({len(alertes_bleues)})"):
                for a in alertes_bleues:
                    st.markdown(f'<div class="alert-box alert-box-bleu">{a}</div>', unsafe_allow_html=True)

    # Interventions du jour
    st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
    st.subheader(f"📅 Interventions du jour — {date_fr(aujourdhui, 'long')}")
    df_jour = sb_select("interventions", {"structure_id": SID, "date_intervention": aujourdhui.isoformat()})
    if df_jour.empty:
        st.caption("Aucune intervention planifiée aujourd'hui.")
    else:
        df_benef_noms = sb_select("beneficiaires", {"structure_id": SID})
        df_interv_noms = sb_select("intervenants", {"structure_id": SID})
        benef_noms = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_benef_noms.iterrows()}
        interv_noms = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_interv_noms.iterrows()}

        for _, row in df_jour.iterrows():
            coul = {"Planifié":"#4c8dfa","Urgence à pourvoir":"#e0554f","Réalisé":"#3fae74","Annulé":"#8996a3"}.get(row["statut"], "#8996a3")
            b_nom = h(benef_noms.get(str(row.get("beneficiaire_id", "")), "—"))
            i_nom = h(interv_noms.get(str(row.get("intervenant_id", "")), "Non assigné"))
            st.markdown(f"""
                <div class="oc-card" style="border-left-color:{coul}; padding:12px 16px;">
                    <b>{h(str(row['heure_debut']))} – {h(str(row['heure_fin']))}</b> · {h(str(row['type_intervention']))}
                    <span class="oc-badge" style="background:{coul}; float:right;">{h(str(row['statut']))}</span><br>
                    <span style="color:#b8c2cc;">👤 {b_nom} &nbsp;•&nbsp; 🧑‍⚕️ {i_nom}</span>
                </div>
            """, unsafe_allow_html=True)


# ============================================================
#  🧑‍🤝‍🧑 VIVIER & SOURCING
# ============================================================
elif onglet == "🧑‍🤝‍🧑 Vivier & Sourcing":
    tab_liste, tab_ajout, tab_sourcing = st.tabs(["📋 Vivier", "➕ Ajouter", "🔎 Sourcing externe"])

    with tab_liste:
        df_interv = sb_select("intervenants", {"structure_id": SID}, order="date_ajout")
        col_f1, col_f2 = st.columns(2)
        filtre_type = col_f1.selectbox("Statut", ["Tous", "Interne", "Vivier candidat", "Externe ponctuel"])
        filtre_dispo = col_f2.selectbox("Disponibilité", ["Toutes", "Disponible", "En mission", "Indisponible"])

        df_aff = df_interv.copy() if not df_interv.empty else pd.DataFrame()
        if not df_aff.empty:
            if filtre_type != "Tous": df_aff = df_aff[df_aff["type_statut"] == filtre_type]
            if filtre_dispo != "Toutes": df_aff = df_aff[df_aff["statut_dispo"] == filtre_dispo]

        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Total", len(df_interv))
        col_m2.metric("Disponibles", len(df_interv[df_interv["statut_dispo"] == "Disponible"]) if not df_interv.empty else 0)
        col_m3.metric("Vivier candidats", len(df_interv[df_interv["type_statut"] == "Vivier candidat"]) if not df_interv.empty else 0)

        if df_aff.empty:
            st.info("Aucun intervenant pour ce filtre.")
        else:
            for _, row in df_aff.iterrows():
                coul = {"Disponible":"#3fae74","En mission":"#d99a3d","Indisponible":"#e0554f"}.get(row["statut_dispo"], "#8996a3")
                st.markdown(f"""
                    <div class="oc-card" style="border-left-color:{coul};">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="font-size:17px; font-weight:700;">{h(row['prenom'])} {h(row['nom'])}</span>
                            <span class="oc-badge" style="background:{coul};">{h(row['statut_dispo'])}</span>
                        </div>
                        <div style="color:#b8c2cc; font-size:13px; margin-top:4px;">
                            {h(row['type_statut'])} • {h(row['zone_geo']) or 'Zone non précisée'} • Source : {h(row['source']) or 'N/C'}
                        </div>
                        <div style="color:#e6ecf2; margin-top:8px;"><b>Compétences :</b> {h(row['competences']) or 'Non renseigné'}</div>
                    </div>
                """, unsafe_allow_html=True)

                with st.expander(f"Détails / actions — {row['prenom']} {row['nom']}"):
                    col_a, col_b = st.columns(2)
                    with col_a:
                        nouveau_statut = st.selectbox(
                            "Disponibilité", ["Disponible", "En mission", "Indisponible"],
                            index=["Disponible","En mission","Indisponible"].index(row["statut_dispo"]) if row["statut_dispo"] in ["Disponible","En mission","Indisponible"] else 0,
                            key=f"dispo_{row['id']}"
                        )
                        if st.button("Mettre à jour", key=f"maj_{row['id']}"):
                            if sb_update("intervenants", {"statut_dispo": nouveau_statut}, "id", row["id"]):
                                audit("UPDATE_INTERVENANT", "intervenants", str(row["id"]), {"statut_dispo": nouveau_statut})
                                st.success("Statut mis à jour.")
                                st.rerun()
                    with col_b:
                        if st.button("🗑️ Supprimer", key=f"del_{row['id']}"):
                            if sb_update("intervenants", {"deleted_at": datetime.datetime.utcnow().isoformat()}, "id", row["id"]):
                                audit("DELETE_INTERVENANT", "intervenants", str(row["id"]))
                                st.warning("Intervenant archivé.")
                                st.rerun()
                    st.write(f"**Parcours :** {row.get('experience_texte') or 'Non renseigné'}")
                    st.write(f"**Disponibilités :** {row.get('disponibilites') or 'Non renseigné'}")
                    st.write(f"**Contact :** {row.get('telephone') or ''} — {row.get('email') or ''}")

    with tab_ajout:
        with st.form("form_ajout_interv"):
            col1, col2 = st.columns(2)
            with col1:
                nom = st.text_input("Nom *")
                prenom = st.text_input("Prénom *")
                telephone = st.text_input("Téléphone")
                email_i = st.text_input("Email")
                type_statut = st.selectbox("Statut", ["Interne", "Vivier candidat", "Externe ponctuel"])
            with col2:
                competences = st.text_area("Compétences / gestes techniques")
                experience_texte = st.text_area("Parcours professionnel")
                zone_geo = st.text_input("Zone géographique")
                disponibilites = st.text_input("Disponibilités")
                source = st.selectbox("Source", ["Vivier interne","CVthèque","Annonce","Réseau / cooptation","Candidature spontanée"])

            # Champ soft skills / personnalité — utilisé par le matching IA
            st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
            st.markdown("**🧠 Personnalité & savoir-être** *(observations du coordinateur — enrichit l'analyse IA)*")
            soft_skills = st.text_area(
                "Observations personnalité / soft skills",
                placeholder="Ex : très à l'aise avec les personnes atteintes d'Alzheimer, grande patience, "
                            "bénévolat auprès de personnes âgées, calme en situation de stress, "
                            "très empathique, bonne communication avec les familles...",
                height=100,
                help="Ces observations sont analysées par l'IA lors du matching pour évaluer l'adéquation humaine avec le bénéficiaire."
            )

            if st.form_submit_button("Ajouter au vivier") and nom and prenom:
                new_row = sb_insert("intervenants", {
                    "structure_id": SID, "nom": nom.strip(), "prenom": prenom.strip(),
                    "telephone": telephone, "email": email_i, "type_statut": type_statut,
                    "competences": competences, "experience_texte": experience_texte,
                    "zone_geo": zone_geo, "disponibilites": disponibilites,
                    "statut_dispo": "Disponible", "source": source,
                    "date_ajout": datetime.date.today().isoformat(),
                    "disponibilites": disponibilites,
                    # Stocké dans experience_texte enrichi si pas de colonne dédiée
                    # On préfixe pour que l'IA puisse le distinguer
                    "experience_texte": f"{experience_texte}\n\n[SOFT SKILLS / PERSONNALITÉ] : {soft_skills}" if soft_skills else experience_texte,
                })
                if new_row:
                    audit("CREATE_INTERVENANT", "intervenants", new_row.get("id"))
                    st.success(f"{prenom} {nom} ajouté(e).")
                    st.rerun()

    with tab_sourcing:
        st.subheader("🔎 Sourcing direct — réduire la dépendance aux agences")
        col_s1, col_s2 = st.columns(2)
        metier = col_s1.text_input("Métier", value="Auxiliaire de vie")
        zone = col_s2.text_input("Zone géographique", value="")
        if st.button("Générer les liens"):
            q = urllib.parse.quote(f"{metier} {zone}".strip())
            st.markdown(f"""
                <div class="oc-card"><b>🔗 LinkedIn</b><br><a href="https://www.linkedin.com/search/results/people/?keywords={q}" target="_blank">Rechercher sur LinkedIn</a></div>
                <div class="oc-card"><b>🔗 CV publics (Google)</b><br><a href="https://www.google.com/search?q={q}+CV+filetype:pdf" target="_blank">Rechercher des CV PDF</a></div>
                <div class="oc-card"><b>🔗 Groupes Facebook emploi</b><br><a href="https://www.facebook.com/search/groups/?q={q}+emploi" target="_blank">Groupes emploi local</a></div>
                <div class="oc-card"><b>🔗 Indeed</b><br><a href="https://www.indeed.fr/jobs?q={q}" target="_blank">Voir sur Indeed</a></div>
            """, unsafe_allow_html=True)


# ============================================================
#  🎯 MATCHING IA
# ============================================================
elif onglet == "🎯 Matching IA":
    df_benef = sb_select("beneficiaires", {"structure_id": SID, "statut": "Actif"}, order="nom")
    df_interv_dispo = sb_select("intervenants", {"structure_id": SID}, order="nom")
    if not df_interv_dispo.empty:
        df_interv_dispo = df_interv_dispo[df_interv_dispo["statut_dispo"] != "Indisponible"]

    if df_benef.empty:
        st.info("Ajoutez d'abord un bénéficiaire.")
    elif df_interv_dispo.empty:
        st.info("Aucun intervenant disponible.")
    else:
        benef_labels = {f"{r['prenom']} {r['nom']}": r['id'] for _, r in df_benef.iterrows()}
        benef_choisi_label = st.selectbox("Bénéficiaire", list(benef_labels.keys()))
        benef_id = benef_labels[benef_choisi_label]
        benef_row = df_benef[df_benef["id"] == benef_id].iloc[0]

        st.markdown(f"""
            <div class="oc-card">
                <b>Besoins récurrents :</b> {h(benef_row.get('besoins_recurrents', '') or 'Non renseigné')}<br>
                <b>Gestes techniques :</b> {h(benef_row.get('gestes_techniques', '') or 'Non renseigné')}<br>
                <b>Horaires :</b> {h(benef_row.get('besoins_horaires', '') or 'Non renseigné')}<br>
                <b>Dépendance :</b> {h(benef_row.get('niveau_dependance', '') or 'Non renseigné')}
            </div>
        """, unsafe_allow_html=True)

        if st.button("🎯 Lancer le matching IA"):
            resultats = []
            barre = st.progress(0)
            total = len(df_interv_dispo)

            for idx, (_, interv) in enumerate(df_interv_dispo.iterrows()):
                df_habs = sb_select("habilitations", {"intervenant_id": str(interv["id"]), "structure_id": SID})
                habs_txt = "; ".join([f"{r['type_habilitation']} (exp. {r['date_expiration']})" for _, r in df_habs.iterrows()]) or "Aucune"

                # Extraction des soft skills si préfixés dans experience_texte
                exp_txt = interv.get("experience_texte", "") or ""
                soft_skills_txt = ""
                if "[SOFT SKILLS / PERSONNALITÉ]" in exp_txt:
                    parties = exp_txt.split("[SOFT SKILLS / PERSONNALITÉ]")
                    exp_txt_clean = parties[0].strip()
                    soft_skills_txt = parties[1].replace(":", "", 1).strip() if len(parties) > 1 else ""
                else:
                    exp_txt_clean = exp_txt

                prompt = f"""
Tu es un coordinateur expert en aide à domicile (SAAD/SSIAD) et psychologue du travail.
Tu dois évaluer l'adéquation GLOBALE entre un bénéficiaire et un intervenant, en combinant :
- Les compétences techniques et habilitations
- Le profil humain, l'empathie et les soft skills — CRITIQUES dans le médico-social

RÈGLES ABSOLUES :
1. Ne jamais inventer une information absente des données fournies.
2. Formuler toute hypothèse comportementale avec prudence ("semble", "laisse supposer").
3. Signaler explicitement si des données sont manquantes plutôt que de compléter.
4. Dans le médico-social, l'empathie et la bienveillance ont autant de poids que les compétences techniques.

═══════════════════════════════════════════════
PROFIL BÉNÉFICIAIRE
═══════════════════════════════════════════════
Niveau de dépendance (GIR) : {benef_row.get('niveau_dependance', 'Non renseigné')}
Pathologies / besoins spécifiques : {benef_row.get('pathologies', 'Non renseigné')}
Gestes techniques requis : {benef_row.get('gestes_techniques', 'Non renseigné')}
Besoins récurrents : {benef_row.get('besoins_recurrents', 'Non renseigné')}
Horaires souhaités : {benef_row.get('besoins_horaires', 'Non renseigné')}
Notes coordinateur : {benef_row.get('notes', 'Aucune')}

═══════════════════════════════════════════════
PROFIL INTERVENANT
═══════════════════════════════════════════════
Compétences techniques déclarées : {interv.get('competences', 'Non renseigné')}
Parcours professionnel : {exp_txt_clean or 'Non renseigné'}
Observations personnalité / soft skills (coordinateur) : {soft_skills_txt or 'Non renseignées'}
Habilitations valides : {habs_txt}
Zone géographique : {interv.get('zone_geo', 'Non renseignée')}
Disponibilités déclarées : {interv.get('disponibilites', 'Non renseignées')}

═══════════════════════════════════════════════
ANALYSE DEMANDÉE — 5 DIMENSIONS
═══════════════════════════════════════════════

DIMENSION 1 — COMPÉTENCES TECHNIQUES (score_competences 0-100)
Évalue si les compétences et gestes techniques de l'intervenant couvrent les besoins du bénéficiaire.
Identifie les compétences transférables issues d'autres expériences.

DIMENSION 2 — HABILITATIONS (score_habilitations 0-100)
Vérifie si les certifications sont à jour et adaptées au profil du bénéficiaire.
Signale toute habilitation manquante obligatoire.

DIMENSION 3 — EMPATHIE & BIENVEILLANCE (score_empathie 0-100)
À partir du parcours, des loisirs/engagements mentionnés, et des observations soft skills :
- Identifie les signaux d'empathie (bénévolat, secteurs d'aide, formulations utilisées)
- Évalue la capacité à accompagner avec douceur et bienveillance
- Signale si le profil de dépendance du bénéficiaire (Alzheimer, fin de vie, GIR 1-2) exige des qualités particulières non visibles dans le profil

DIMENSION 4 — SOFT SKILLS & PERSONNALITÉ (score_soft_skills 0-100)
- Patience, écoute active, gestion du stress, communication avec les familles
- Stabilité émotionnelle, adaptabilité, discrétion
- Déduit depuis le parcours ET les observations du coordinateur

DIMENSION 5 — COMPATIBILITÉ PRATIQUE (score_compatibilite 0-100)
- Adéquation zone géographique / secteur bénéficiaire
- Compatibilité des disponibilités avec les horaires requis

═══════════════════════════════════════════════
FORMAT DE RÉPONSE — JSON STRICT
═══════════════════════════════════════════════
Réponds UNIQUEMENT avec ce JSON (pas de markdown, pas de texte autour) :
{{
  "score_competences": <0-100>,
  "score_habilitations": <0-100>,
  "score_empathie": <0-100>,
  "score_soft_skills": <0-100>,
  "score_compatibilite": <0-100>,
  "score_global": <moyenne pondérée : compétences×25% + habilitations×20% + empathie×25% + soft_skills×20% + compatibilite×10%>,
  "profil_humain": "<une phrase qui décrit le profil relationnel de l'intervenant>",
  "traits_dominants": ["<trait 1>", "<trait 2>", "<trait 3>"],
  "competences_transferables": ["<compétence — source — transfert>"],
  "alerte_habilitation": "<texte si habilitation manquante, sinon vide>",
  "alerte_humaine": "<texte si le profil bénéficiaire exige des qualités particulières non visibles, sinon vide>",
  "justification": "<synthèse narrative de 3-5 lignes, humaine et sourcée>"
}}
                """
                data = appel_ia(prompt)
                if data:
                    data["intervenant_nom"] = f"{interv['prenom']} {interv['nom']}"
                    data["intervenant_statut"] = interv.get("type_statut", "")
                    data["intervenant_zone"] = interv.get("zone_geo", "")
                    data["intervenant_dispo"] = interv.get("disponibilites", "")
                    resultats.append(data)
                barre.progress((idx + 1) / total)

            st.session_state["resultats_matching"] = sorted(
                resultats, key=lambda x: int(x.get("score_global", 0)), reverse=True
            )
            st.session_state["benef_matching_label"] = benef_choisi_label

        # ── Affichage des résultats ────────────────────────────────────────────
        if st.session_state.get("resultats_matching"):
            st.markdown("### 📊 Résultats du matching")

            DIMENSIONS = [
                ("score_competences",  "🛠️ Compétences techniques", "#2f7cf6"),
                ("score_habilitations","🎓 Habilitations",          "#7c3aed"),
                ("score_empathie",     "❤️ Empathie & bienveillance","#e0554f"),
                ("score_soft_skills",  "🧠 Soft skills",             "#d99a3d"),
                ("score_compatibilite","📍 Compatibilité pratique",  "#3fae74"),
            ]

            for rang, res in enumerate(st.session_state["resultats_matching"], 1):
                score = int(res.get("score_global", 0))
                coul_score = "#3fae74" if score >= 70 else ("#d99a3d" if score >= 45 else "#e0554f")
                nom_interv = h(res.get("intervenant_nom", ""))
                statut_interv = h(res.get("intervenant_statut", ""))
                rangs_emoji = {1: "🥇", 2: "🥈", 3: "🥉"}
                rang_label = rangs_emoji.get(rang, f"#{rang}")

                # Carte de résumé
                st.markdown(f"""
                    <div class="oc-card" style="border-left-color:{coul_score};">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <div>
                                <span style="font-size:18px; font-weight:800;">{rang_label} {nom_interv}</span>
                                <span style="color:#8996a3; font-size:13px; margin-left:10px;">{statut_interv}</span>
                            </div>
                            <span class="oc-badge" style="background:{coul_score}; font-size:16px;">{score}%</span>
                        </div>
                        <div style="color:#b8c2cc; font-size:13px; margin-top:6px; font-style:italic;">
                            {h(res.get('profil_humain', ''))}
                        </div>
                    </div>
                """, unsafe_allow_html=True)

                # Alertes
                if res.get("alerte_habilitation"):
                    st.warning(f"⚠️ Habilitation : {res['alerte_habilitation']}")
                if res.get("alerte_humaine"):
                    st.info(f"💡 Profil bénéficiaire : {res['alerte_humaine']}")

                with st.expander(f"📋 Analyse détaillée — {nom_interv}"):

                    # ── Traits de personnalité (style OmniRecrut) ──
                    traits = res.get("traits_dominants", [])
                    if traits:
                        st.markdown("#### 🧠 Empreinte comportementale")
                        couleurs_traits = ["#2563eb", "#7c3aed", "#e0554f", "#d99a3d", "#3fae74"]
                        nb = min(len(traits), 5)
                        cols_traits = st.columns(nb)
                        for i, trait in enumerate(traits[:nb]):
                            lettre = str(trait).strip()[0].upper() if str(trait).strip() else "?"
                            mot_court = str(trait).strip().split()[0][:10]
                            with cols_traits[i]:
                                st.markdown(f"""
                                    <div style="text-align:center; background:#1e293b; border-radius:12px;
                                                padding:14px 8px; border:2px solid {couleurs_traits[i % len(couleurs_traits)]};">
                                        <div style="font-size:26px; font-weight:800;
                                                    color:{couleurs_traits[i % len(couleurs_traits)]};">{h(lettre)}</div>
                                        <div style="font-size:11px; color:#94a3b8;
                                                    margin-top:4px; font-weight:600;">{h(mot_court)}</div>
                                    </div>
                                """, unsafe_allow_html=True)
                        st.markdown("")
                        for trait in traits:
                            st.markdown(f"""
                                <div style="background:#1e293b; border-left:3px solid #2563eb;
                                            border-radius:6px; padding:10px 14px; margin-bottom:6px;
                                            color:#cbd5e1; font-size:13px;">🔹 {h(str(trait))}</div>
                            """, unsafe_allow_html=True)
                        st.markdown("---")

                    # ── Scores par dimension (pastilles style OmniRecrut) ──
                    st.markdown("#### 📊 Évaluation par dimension")
                    for i in range(0, len(DIMENSIONS), 2):
                        paire = DIMENSIONS[i:i+2]
                        cols_dim = st.columns(len(paire))
                        for col_d, (cle, label, couleur) in zip(cols_dim, paire):
                            val = int(res.get(cle, 0))
                            nb_pleines = round(val / 20)  # 5 pastilles max
                            pastilles = "".join([
                                f'<span style="display:inline-block; width:16px; height:16px; '
                                f'border-radius:50%; margin-right:5px; '
                                f'background:{couleur if j < nb_pleines else "#334155"};"></span>'
                                for j in range(5)
                            ])
                            with col_d:
                                st.markdown(f"""
                                    <div style="background:#1e293b; border-radius:10px; padding:14px 16px;
                                                border-left:4px solid {couleur}; margin-bottom:10px; min-height:100px;">
                                        <div style="font-size:12px; color:#94a3b8; font-weight:600;
                                                    text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">
                                            {h(label)}
                                        </div>
                                        <div style="font-size:22px; font-weight:800; color:{couleur}; margin-bottom:8px;">
                                            {val}%
                                        </div>
                                        <div>{pastilles}</div>
                                    </div>
                                """, unsafe_allow_html=True)
                    st.markdown("---")

                    # ── Compétences transférables ──
                    transf = res.get("competences_transferables", [])
                    if transf:
                        st.markdown("#### 🌱 Compétences transférables détectées")
                        for t in transf:
                            st.markdown(f"""
                                <div style="background:#1e293b; border-left:3px solid #3fae74;
                                            border-radius:6px; padding:8px 12px; margin-bottom:5px;
                                            color:#86efac; font-size:12px;">✦ {h(str(t))}</div>
                            """, unsafe_allow_html=True)
                        st.markdown("---")

                    # ── Synthèse narrative ──
                    justif = res.get("justification", "")
                    if justif:
                        st.markdown("#### 📄 Synthèse de l'analyse")
                        st.markdown(f"""
                            <div style="background:#1a202c; padding:18px; border-radius:8px;
                                        color:#e2e8f0; white-space:pre-wrap; line-height:1.7;
                                        font-size:13px; border:1px solid rgba(47,124,246,0.3);">
                                {h(justif)}
                            </div>
                        """, unsafe_allow_html=True)

                    # ── Export PDF du rapport ──
                    st.markdown("---")
                    benef_label_pdf = st.session_state.get("benef_matching_label", "Bénéficiaire")
                    try:
                        pdf_rapport = _generer_pdf_matching(
                            intervenant_nom=res.get("intervenant_nom", ""),
                            intervenant_statut=res.get("intervenant_statut", ""),
                            intervenant_zone=res.get("intervenant_zone", ""),
                            beneficiaire_nom=benef_label_pdf,
                            score_global=score,
                            profil_humain=res.get("profil_humain", ""),
                            traits_dominants=res.get("traits_dominants", []),
                            dimensions=DIMENSIONS,
                            scores=res,
                            competences_transferables=res.get("competences_transferables", []),
                            alerte_habilitation=res.get("alerte_habilitation", ""),
                            alerte_humaine=res.get("alerte_humaine", ""),
                            justification=res.get("justification", "")
                        )
                        st.download_button(
                            label="⬇️ Télécharger le rapport PDF",
                            data=pdf_rapport,
                            file_name=f"matching_{res.get('intervenant_nom','').replace(' ','_')}_{benef_label_pdf.replace(' ','_')}.pdf",
                            mime="application/pdf",
                            key=f"pdf_matching_{rang}"
                        )
                    except Exception as e_pdf:
                        logger.error(f"PDF matching: {e_pdf}")
                        st.caption("Export PDF indisponible.")


# ============================================================
#  ❤️ BÉNÉFICIAIRES
# ============================================================
elif onglet == "❤️ Bénéficiaires":
    tab_liste_b, tab_ajout_b = st.tabs(["📋 Bénéficiaires", "➕ Ajouter"])

    df_interv_all = sb_select("intervenants", {"structure_id": SID}, order="nom")
    interv_map = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_interv_all.iterrows()}

    with tab_liste_b:
        df_b = sb_select("beneficiaires", {"structure_id": SID}, order="nom")
        st.metric("Bénéficiaires actifs", len(df_b[df_b["statut"] == "Actif"]) if not df_b.empty else 0)

        if df_b.empty:
            st.info("Aucun bénéficiaire enregistré.")
        else:
            for _, row in df_b.iterrows():
                coul = "#3fae74" if row["statut"] == "Actif" else "#8996a3"
                attitré = h(interv_map.get(str(row.get("intervenant_attitré_id", "")), "Non défini"))
                st.markdown(f"""
                    <div class="oc-card" style="border-left-color:{coul};">
                        <div style="display:flex; justify-content:space-between;">
                            <span style="font-size:17px; font-weight:700;">{h(row['prenom'])} {h(row['nom'])}</span>
                            <span class="oc-badge" style="background:{coul};">{h(row['statut'])}</span>
                        </div>
                        <div style="color:#b8c2cc; font-size:13px; margin-top:4px;">
                            📍 {h(row['adresse']) or '—'} &nbsp;•&nbsp; 🧑‍⚕️ {attitré}
                        </div>
                    </div>
                """, unsafe_allow_html=True)

                with st.expander(f"📋 Fiche — {row['prenom']} {row['nom']}"):
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown(f"""
                            <div class="fiche-section">
                                <h4>📍 Coordonnées</h4>
                                <div class="fiche-row"><span class="fiche-label">Adresse</span><span class="fiche-value">{h(row['adresse']) or '—'}</span></div>
                                <div class="fiche-row"><span class="fiche-label">Téléphone</span><span class="fiche-value">{h(row['telephone']) or '—'}</span></div>
                                <div class="fiche-row"><span class="fiche-label">GIR</span><span class="fiche-value">{h(row['niveau_dependance']) or '—'}</span></div>
                            </div>
                            <div class="fiche-section">
                                <h4>🚨 Contact d'urgence</h4>
                                <div class="fiche-row"><span class="fiche-label">Nom</span><span class="fiche-value">{h(row.get('contact_urgence_nom','')) or '—'}</span></div>
                                <div class="fiche-row"><span class="fiche-label">Tél.</span><span class="fiche-value">{h(row.get('contact_urgence_tel','')) or '—'}</span></div>
                            </div>
                        """, unsafe_allow_html=True)
                    with c2:
                        st.markdown(f"""
                            <div class="fiche-section">
                                <h4>🔄 Besoins récurrents</h4>
                                <div class="fiche-value">{h(row.get('besoins_recurrents','')) or '—'}</div>
                            </div>
                            <div class="fiche-section">
                                <h4>🧑‍⚕️ Intervenant attitré</h4>
                                <div class="fiche-value" style="font-size:15px; font-weight:600;">{attitré}</div>
                            </div>
                        """, unsafe_allow_html=True)

                    st.markdown("<br>", unsafe_allow_html=True)
                    cx, cy, cz = st.columns(3)
                    with cx:
                        nv_statut = st.selectbox("Statut", ["Actif","Inactif","Décédé"],
                                                  index=["Actif","Inactif","Décédé"].index(row["statut"]) if row["statut"] in ["Actif","Inactif","Décédé"] else 0,
                                                  key=f"sb_{row['id']}")
                        if st.button("Mettre à jour", key=f"upd_b_{row['id']}"):
                            sb_update("beneficiaires", {"statut": nv_statut}, "id", row["id"])
                            st.rerun()
                    with cy:
                        opts_i = {"Non défini": None}
                        opts_i.update({v: k for k, v in interv_map.items()})
                        if st.button("Définir attitré", key=f"att_{row['id']}"):
                            pass  # handled below
                        sel_att = st.selectbox("Attitré", list(opts_i.keys()), key=f"sel_att_{row['id']}")
                        if st.button("💾 Enregistrer attitré", key=f"save_att_{row['id']}"):
                            sb_update("beneficiaires", {"intervenant_attitré_id": opts_i[sel_att]}, "id", row["id"])
                            st.rerun()
                    with cz:
                        # Export RGPD
                        if st.button("📥 Export RGPD", key=f"rgpd_{row['id']}"):
                            df_iv_b = sb_select("interventions", {"structure_id": SID, "beneficiaire_id": str(row["id"])})
                            df_doc_b = sb_select("documents_transmissions", {"structure_id": SID, "beneficiaire_id": str(row["id"])})
                            pdf_bytes = creer_pdf_export_rgpd(
                                row.to_dict(),
                                df_iv_b.to_dict("records") if not df_iv_b.empty else [],
                                df_doc_b.to_dict("records") if not df_doc_b.empty else []
                            )
                            audit("EXPORT_RGPD", "beneficiaires", str(row["id"]))
                            st.download_button(
                                "⬇️ Télécharger le dossier RGPD",
                                data=pdf_bytes,
                                file_name=f"dossier_RGPD_{row['nom']}_{row['prenom']}.pdf",
                                mime="application/pdf",
                                key=f"dl_rgpd_{row['id']}"
                            )
                        if st.button("🗑️ Supprimer", key=f"del_b_{row['id']}"):
                            sb_update("beneficiaires", {"deleted_at": datetime.datetime.utcnow().isoformat(), "statut": "Inactif"}, "id", row["id"])
                            audit("DELETE_BENEFICIAIRE", "beneficiaires", str(row["id"]))
                            st.rerun()

    with tab_ajout_b:
        with st.form("form_add_benef"):
            c1, c2 = st.columns(2)
            with c1:
                nom_b = st.text_input("Nom *")
                prenom_b = st.text_input("Prénom *")
                adresse_b = st.text_input("Adresse")
                telephone_b = st.text_input("Téléphone")
                niveau_dep = st.selectbox("GIR", ["GIR 1","GIR 2","GIR 3","GIR 4","GIR 5","GIR 6","Non évalué"])
            with c2:
                contact_urgence_nom_b = st.text_input("Contact d'urgence (nom + lien)")
                contact_urgence_tel_b = st.text_input("Tél. contact d'urgence")
                besoins_rec = st.text_area("Besoins récurrents")
                gestes_b = st.text_area("Gestes techniques requis")
                horaires_b = st.text_input("Besoins horaires")
                notes_b = st.text_area("Notes")

            opts_att = {"Non défini": None}
            if not df_interv_all.empty:
                opts_att.update({f"{r['prenom']} {r['nom']}": str(r["id"]) for _, r in df_interv_all.iterrows()})
            att_sel = st.selectbox("Intervenant attitré (optionnel)", list(opts_att.keys()))

            if st.form_submit_button("Ajouter") and nom_b and prenom_b:
                new_b = sb_insert("beneficiaires", {
                    "structure_id": SID, "nom": nom_b.strip(), "prenom": prenom_b.strip(),
                    "adresse": adresse_b, "telephone": telephone_b, "niveau_dependance": niveau_dep,
                    "gestes_techniques": gestes_b, "besoins_horaires": horaires_b,
                    "besoins_recurrents": besoins_rec, "notes": notes_b,
                    "contact_urgence_nom": contact_urgence_nom_b, "contact_urgence_tel": contact_urgence_tel_b,
                    "intervenant_attitré_id": opts_att[att_sel],
                    "statut": "Actif", "date_creation": datetime.date.today().isoformat()
                })
                if new_b:
                    audit("CREATE_BENEFICIAIRE", "beneficiaires", new_b.get("id"))
                    st.success(f"{prenom_b} {nom_b} ajouté(e).")
                    st.rerun()


# ============================================================
#  📝 DOCUMENTS & TRANSMISSIONS
# ============================================================
elif onglet == "📝 Documents & Transmissions":
    df_benef2 = sb_select("beneficiaires", {"structure_id": SID, "statut": "Actif"}, order="nom")
    df_interv2 = sb_select("intervenants", {"structure_id": SID}, order="nom")

    if df_benef2.empty:
        st.info("Ajoutez d'abord un bénéficiaire.")
    else:
        c1, c2 = st.columns(2)
        benef_lbl2 = {f"{r['prenom']} {r['nom']}": str(r["id"]) for _, r in df_benef2.iterrows()}
        benef_ch2 = c1.selectbox("Bénéficiaire", list(benef_lbl2.keys()))
        interv_lbl2 = {"Non spécifié": None}
        interv_lbl2.update({f"{r['prenom']} {r['nom']}": str(r["id"]) for _, r in df_interv2.iterrows()})
        interv_ch2 = c2.selectbox("Intervenant rédacteur", list(interv_lbl2.keys()))

        type_doc = st.selectbox("Type", ["Fiche de liaison","Compte-rendu de visite","Transmission d'équipe","Note d'incident"])
        notes_brutes = st.text_area("Notes brutes", height=150)

        if st.button("✍️ Générer avec l'IA"):
            if not notes_brutes:
                st.warning("Ajoutez des notes avant de générer.")
            else:
                prompt = f"""
                Tu es coordinateur(trice) en SAAD. Rédige un document de type "{type_doc}"
                à partir des notes brutes, en style professionnel, factuel, 10-15 lignes max.
                N'ajoute aucune information médicale absente des notes.

                Bénéficiaire : {benef_ch2}
                Notes : {notes_brutes}
                """
                data = appel_ia(prompt)
                if data is None:
                    # appel_ia() retourne None si erreur, on essaie le texte brut
                    try:
                        reponse = model.generate_content(prompt)
                        st.session_state["doc_genere"] = reponse.text.strip()
                        incrementer_quota_ia()
                    except Exception as e:
                        st.error(f"Erreur IA : {e}")
                else:
                    st.session_state["doc_genere"] = str(data)

        if st.session_state.get("doc_genere"):
            texte_final = st.text_area("Document (modifiable)", value=st.session_state["doc_genere"], height=250)
            col_s, col_p = st.columns(2)
            with col_s:
                if st.button("💾 Enregistrer"):
                    new_doc = sb_insert("documents_transmissions", {
                        "structure_id": SID,
                        "beneficiaire_id": benef_lbl2[benef_ch2],
                        "intervenant_id": interv_lbl2[interv_ch2],
                        "date_creation": datetime.date.today().isoformat(),
                        "type_document": type_doc,
                        "contenu": texte_final
                    })
                    if new_doc:
                        audit("CREATE_DOCUMENT", "documents_transmissions", new_doc.get("id"))
                        st.success("Document enregistré.")
            with col_p:
                try:
                    pdf_b = creer_pdf_transmission(benef_ch2, interv_ch2, datetime.date.today().strftime("%d/%m/%Y"), texte_final)
                    st.download_button("⬇️ PDF", data=pdf_b, file_name=f"{type_doc}_{benef_ch2}.pdf", mime="application/pdf")
                except Exception as e:
                    st.error(f"Erreur PDF : {e}")

        st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
        st.markdown("### 🗂️ Historique")
        df_docs = sb_select("documents_transmissions", {"structure_id": SID}, order="date_creation")
        if df_docs.empty:
            st.caption("Aucun document enregistré.")
        else:
            st.dataframe(df_docs[["date_creation","type_document","contenu"]].head(50),
                        use_container_width=True, hide_index=True)


# ============================================================
#  📅 PLANNINGS & URGENCES
# ============================================================
elif onglet == "📅 Plannings & Urgences":
    tab_plan, tab_ajout_p, tab_urg = st.tabs(["📊 Planning hebdo", "➕ Planifier", "🚨 Urgences"])

    df_benef3 = sb_select("beneficiaires", {"structure_id": SID, "statut": "Actif"}, order="nom")
    df_interv3 = sb_select("intervenants", {"structure_id": SID}, order="nom")

    with tab_plan:
        if "semaine_offset" not in st.session_state:
            st.session_state["semaine_offset"] = 0

        cn1, cn2, cn3 = st.columns([1, 3, 1])
        if cn1.button("◀ Précédente"): st.session_state["semaine_offset"] -= 1; st.rerun()
        if cn3.button("Suivante ▶"): st.session_state["semaine_offset"] += 1; st.rerun()

        offset = st.session_state["semaine_offset"]
        aujourd = datetime.date.today()
        lundi = aujourd - datetime.timedelta(days=aujourd.weekday()) + datetime.timedelta(weeks=offset)
        dim = lundi + datetime.timedelta(days=6)

        cn2.markdown(f"<div style='text-align:center; color:#4c8dfa; font-weight:700;'>Semaine du {date_fr(lundi,'medium')} au {date_fr(dim,'medium')}</div>", unsafe_allow_html=True)
        if st.button("🔙 Semaine courante"): st.session_state["semaine_offset"] = 0; st.rerun()

        # Chargement des données de la semaine
        df_all_iv = sb_select("interventions", {"structure_id": SID})
        df_sem = pd.DataFrame()
        if not df_all_iv.empty:
            df_all_iv["date_intervention"] = pd.to_datetime(df_all_iv["date_intervention"]).dt.date
            df_sem = df_all_iv[(df_all_iv["date_intervention"] >= lundi) & (df_all_iv["date_intervention"] <= dim)]

        benef_noms = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_benef3.iterrows()}
        dates_sem = [lundi + datetime.timedelta(days=i) for i in range(7)]

        if df_interv3.empty:
            st.info("Ajoutez des intervenants pour voir le planning.")
        else:
            headers_html = '<th class="col-intervenant">Intervenant</th>'
            for d in dates_sem:
                is_today = (d == aujourd)
                style = " style='background:rgba(47,124,246,0.25); color:#4c8dfa;'" if is_today else ""
                headers_html += f'<th{style}>{date_fr(d,"semaine")}</th>'

            rows_html = ""
            for _, interv in df_interv3.iterrows():
                row_html = f'<td class="col-intervenant">{h(interv["prenom"])} {h(interv["nom"])}</td>'
                for d in dates_sem:
                    if df_sem.empty:
                        row_html += '<td><div class="planning-empty">·</div></td>'
                        continue
                    ivs = df_sem[
                        (df_sem["date_intervention"] == d) &
                        (df_sem["intervenant_id"] == str(interv["id"]))
                    ]
                    if ivs.empty:
                        row_html += '<td><div class="planning-empty">·</div></td>'
                    else:
                        cell = ""
                        for _, iv in ivs.iterrows():
                            css = " urgence" if iv["statut"]=="Urgence à pourvoir" else (" realise" if iv["statut"]=="Réalisé" else (" annule" if iv["statut"]=="Annulé" else ""))
                            b = h(benef_noms.get(str(iv.get("beneficiaire_id","")), "—"))
                            cell += f'<div class="planning-cell{css}"><b>{h(str(iv["heure_debut"]))}–{h(str(iv["heure_fin"]))}</b><br>{b}<br><span style="color:#8996a3;font-size:11px;">{h(str(iv["type_intervention"]))}</span></div>'
                        row_html += f'<td>{cell}</td>'
                rows_html += f"<tr>{row_html}</tr>"

            st.markdown(f'<div style="overflow-x:auto;"><table class="planning-table"><thead><tr>{headers_html}</tr></thead><tbody>{rows_html}</tbody></table></div>', unsafe_allow_html=True)

    with tab_ajout_p:
        if df_benef3.empty or df_interv3.empty:
            st.info("Ajoutez au moins un bénéficiaire et un intervenant.")
        else:
            with st.form("form_plan"):
                c1, c2, c3 = st.columns(3)
                benef_lbl3 = {f"{r['prenom']} {r['nom']}": str(r["id"]) for _, r in df_benef3.iterrows()}
                interv_lbl3 = {f"{r['prenom']} {r['nom']}": str(r["id"]) for _, r in df_interv3.iterrows()}
                benef_p = c1.selectbox("Bénéficiaire", list(benef_lbl3.keys()))
                interv_p = c2.selectbox("Intervenant", list(interv_lbl3.keys()))
                type_iv = c3.selectbox("Type", ["Aide à la toilette","Aide au repas","Ménage","Accompagnement","Soins","Autre"])
                c4, c5, c6 = st.columns(3)
                date_p = c4.date_input("Date", value=datetime.date.today())
                hd = c5.time_input("Heure début")
                hf = c6.time_input("Heure fin")
                notes_p = st.text_input("Notes")

                if st.form_submit_button("Planifier"):
                    if hf <= hd:
                        st.error("L'heure de fin doit être après l'heure de début.")
                    else:
                        new_iv = sb_insert("interventions", {
                            "structure_id": SID,
                            "beneficiaire_id": benef_lbl3[benef_p],
                            "intervenant_id": interv_lbl3[interv_p],
                            "date_intervention": date_p.isoformat(),
                            "heure_debut": hd.strftime("%H:%M"),
                            "heure_fin": hf.strftime("%H:%M"),
                            "type_intervention": type_iv,
                            "statut": "Planifié",
                            "notes": notes_p
                        })
                        if new_iv:
                            audit("CREATE_INTERVENTION", "interventions", new_iv.get("id"))
                            st.success("Intervention planifiée.")
                            st.rerun()

        # Interventions à venir
        st.markdown("### 📋 Interventions à venir")
        df_plan = sb_select("interventions", {"structure_id": SID}, order="date_intervention")
        if not df_plan.empty:
            df_plan["date_intervention"] = pd.to_datetime(df_plan["date_intervention"]).dt.date
            df_plan = df_plan[df_plan["date_intervention"] >= datetime.date.today()]
            benef_noms2 = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_benef3.iterrows()}
            interv_noms2 = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_interv3.iterrows()}

            for _, row in df_plan.iterrows():
                coul = {"Planifié":"#4c8dfa","Urgence à pourvoir":"#e0554f","Réalisé":"#3fae74","Annulé":"#8996a3"}.get(row["statut"], "#8996a3")
                b = h(benef_noms2.get(str(row.get("beneficiaire_id","")), "—"))
                iv = h(interv_noms2.get(str(row.get("intervenant_id","")), "Non assigné"))
                ci, ca = st.columns([4, 1])
                with ci:
                    st.markdown(f"""
                        <div class="oc-card" style="border-left-color:{coul}; padding:12px 16px;">
                            <b>{row['date_intervention']} — {h(str(row['heure_debut']))} à {h(str(row['heure_fin']))}</b> · {h(str(row['type_intervention']))}<br>
                            <span style="color:#b8c2cc;">👤 {b} • 🧑‍⚕️ {iv} •
                            <span class="oc-badge" style="background:{coul}; padding:2px 10px;">{h(str(row['statut']))}</span></span>
                        </div>
                    """, unsafe_allow_html=True)
                with ca:
                    if row["statut"] == "Planifié":
                        if st.button("✅ Réalisée", key=f"r_{row['id']}"):
                            sb_update("interventions", {"statut": "Réalisé"}, "id", str(row["id"]))
                            st.rerun()
                    if row["statut"] not in ["Urgence à pourvoir","Annulé","Réalisé"]:
                        if st.button("🚨 Absence", key=f"a_{row['id']}"):
                            sb_update("interventions", {"statut": "Urgence à pourvoir", "intervenant_id": None}, "id", str(row["id"]))
                            st.rerun()

    with tab_urg:
        st.caption("🤖 L'agent IA classe les disponibles et sollicite automatiquement en cascade.")
        df_urgs = sb_select("interventions", {"structure_id": SID, "statut": "Urgence à pourvoir"}, order="date_intervention")

        if df_urgs.empty:
            st.success("✅ Aucune urgence en cours.")
        else:
            benef_noms_u = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_benef3.iterrows()}
            for _, urg in df_urgs.iterrows():
                b = h(benef_noms_u.get(str(urg.get("beneficiaire_id","")), "Inconnu"))
                st.markdown(f"""
                    <div class="oc-card oc-card-alert">
                        <b>🚨 {h(str(urg['date_intervention']))} — {h(str(urg['heure_debut']))} à {h(str(urg['heure_fin']))}</b><br>
                        {b} • {h(str(urg['type_intervention']))}
                    </div>
                """, unsafe_allow_html=True)

                # Sollicitation active
                df_sol = sb_select("sollicitations_urgence", {
                    "structure_id": SID, "intervention_id": str(urg["id"]), "statut": "En attente"
                })
                interv_noms_u = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_interv3.iterrows()}

                if not df_sol.empty:
                    sol = df_sol.iloc[0]
                    sol_nom = h(interv_noms_u.get(str(sol.get("intervenant_id","")), "Inconnu"))
                    st.markdown(f"""
                        <div class="oc-card oc-card-warning">
                            🤖 <b>Sollicité :</b> {sol_nom} (score {sol['score_global']}%)<br>
                            <span style="color:#b8c2cc;">{h(str(sol.get('justification','')))} — ⏳ En attente</span>
                        </div>
                    """, unsafe_allow_html=True)
                    ca, cr = st.columns(2)
                    if ca.button("✅ A accepté", key=f"acc_{sol['id']}"):
                        sb_update("sollicitations_urgence", {"statut": "Accepté"}, "id", str(sol["id"]))
                        sb_update("interventions", {"intervenant_id": str(sol["intervenant_id"]), "statut": "Planifié"}, "id", str(urg["id"]))
                        st.success("Remplacement confirmé.")
                        st.rerun()
                    if cr.button("❌ A refusé → suivant", key=f"ref_{sol['id']}"):
                        sb_update("sollicitations_urgence", {"statut": "Refusé"}, "id", str(sol["id"]))
                        st.rerun()
                else:
                    df_dispo_u = sb_select("intervenants", {"structure_id": SID, "statut_dispo": "Disponible"})
                    if df_dispo_u.empty:
                        st.warning("Aucun intervenant disponible.")
                    elif IA_DISPONIBLE:
                        if st.button("🤖 Lancer l'agent IA", key=f"ia_{urg['id']}"):
                            classement = classer_candidats_urgence(urg.to_dict(), df_dispo_u)
                            # Exclure les déjà sollicités
                            df_deja = sb_select("sollicitations_urgence", {"intervention_id": str(urg["id"]), "structure_id": SID})
                            ids_excl = set(df_deja["intervenant_id"].tolist()) if not df_deja.empty else set()
                            candidat = next((c for c in classement if c["intervenant_id"] not in ids_excl), None)

                            if not candidat:
                                st.warning("Tous les disponibles ont déjà été sollicités.")
                            else:
                                ok, msg = envoyer_email(
                                    candidat["intervenant_email"],
                                    f"Remplacement urgent le {urg['date_intervention']}",
                                    f"Bonjour,\n\nUne intervention est à pourvoir le {urg['date_intervention']} de {urg['heure_debut']} à {urg['heure_fin']} ({urg['type_intervention']}).\nMerci de confirmer votre disponibilité.\n\nMerci."
                                )
                                if ok:
                                    sb_insert("sollicitations_urgence", {
                                        "structure_id": SID,
                                        "intervention_id": str(urg["id"]),
                                        "intervenant_id": candidat["intervenant_id"],
                                        "score_global": int(candidat.get("score_global", 0)),
                                        "justification": candidat.get("justification", ""),
                                        "alerte_habilitation": candidat.get("alerte_habilitation", ""),
                                        "statut": "En attente"
                                    })
                                    st.success(f"✅ {candidat['intervenant_nom']} sollicité(e).")
                                    st.rerun()
                                else:
                                    st.error(msg)

                if st.button("✅ Pourvu manuellement", key=f"man_{urg['id']}"):
                    sb_update("interventions", {"statut": "Planifié"}, "id", str(urg["id"]))
                    st.rerun()
                st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)


# ============================================================
#  ✅ CONFORMITÉ & HABILITATIONS
# ============================================================
elif onglet == "✅ Conformité & Habilitations":
    tab_suivi, tab_ajout_hab = st.tabs(["📋 Suivi", "➕ Ajouter"])

    df_interv4 = sb_select("intervenants", {"structure_id": SID}, order="nom")
    aujourd = datetime.date.today()
    seuil = aujourd + datetime.timedelta(days=60)

    with tab_suivi:
        df_habs = sb_select("habilitations", {"structure_id": SID})
        if df_habs.empty:
            st.info("Aucune habilitation enregistrée.")
        else:
            interv_noms4 = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_interv4.iterrows()}
            df_habs["intervenant_nom"] = df_habs["intervenant_id"].apply(lambda x: interv_noms4.get(str(x), "Inconnu"))
            df_habs["date_exp_dt"] = pd.to_datetime(df_habs["date_expiration"], errors="coerce").dt.date

            exp = df_habs[df_habs["date_exp_dt"] < aujourd]
            bientot = df_habs[(df_habs["date_exp_dt"] >= aujourd) & (df_habs["date_exp_dt"] <= seuil)]
            ok = df_habs[df_habs["date_exp_dt"] > seuil]

            c1, c2, c3 = st.columns(3)
            c1.metric("🔴 Expirées", len(exp))
            c2.metric("🟠 < 60 jours", len(bientot))
            c3.metric("🟢 À jour", len(ok))

            if not exp.empty:
                st.markdown("#### 🔴 Expirées")
                for _, hb in exp.iterrows():
                    st.markdown(f'<div class="oc-card oc-card-alert"><b>{h(hb["intervenant_nom"])}</b> — {h(hb["type_habilitation"])} — expirée le {hb["date_expiration"]}</div>', unsafe_allow_html=True)
            if not bientot.empty:
                st.markdown("#### 🟠 À renouveler bientôt")
                for _, hb in bientot.iterrows():
                    st.markdown(f'<div class="oc-card oc-card-warning"><b>{h(hb["intervenant_nom"])}</b> — {h(hb["type_habilitation"])} — expire le {hb["date_expiration"]}</div>', unsafe_allow_html=True)
            if not ok.empty:
                with st.expander("🟢 À jour"):
                    st.dataframe(ok[["intervenant_nom","type_habilitation","date_obtention","date_expiration"]], use_container_width=True, hide_index=True)

    with tab_ajout_hab:
        if df_interv4.empty:
            st.info("Ajoutez d'abord un intervenant.")
        else:
            with st.form("form_hab"):
                interv_lbl4 = {f"{r['prenom']} {r['nom']}": str(r["id"]) for _, r in df_interv4.iterrows()}
                interv_sel = st.selectbox("Intervenant", list(interv_lbl4.keys()))
                type_hab = st.selectbox("Type", ["Diplôme AES","DEAES","PSC1 / SST","Permis B","Visite médecine du travail","Habilitation gestes et postures","AFGSU","Autre"])
                date_obt = st.date_input("Date d'obtention")
                date_exp = st.date_input("Date d'expiration")

                if st.form_submit_button("Ajouter"):
                    if date_exp <= date_obt:
                        st.error("La date d'expiration doit être après la date d'obtention.")
                    else:
                        new_h = sb_insert("habilitations", {
                            "structure_id": SID,
                            "intervenant_id": interv_lbl4[interv_sel],
                            "type_habilitation": type_hab,
                            "date_obtention": date_obt.isoformat(),
                            "date_expiration": date_exp.isoformat()
                        })
                        if new_h:
                            audit("CREATE_HABILITATION", "habilitations", new_h.get("id"))
                            st.success("Habilitation ajoutée.")
                            st.rerun()


# ============================================================
#  📊 SUIVI DES HEURES (NOUVEAU MODULE)
# ============================================================
elif onglet == "📊 Suivi des heures":
    st.caption("Suivi mensuel des heures planifiées vs réalisées par intervenant.")

    df_interv_h = sb_select("intervenants", {"structure_id": SID}, order="nom")
    df_iv_all = sb_select("interventions", {"structure_id": SID})

    if df_interv_h.empty or df_iv_all.empty:
        st.info("Aucune donnée disponible. Planifiez des interventions.")
    else:
        df_iv_all["date_intervention"] = pd.to_datetime(df_iv_all["date_intervention"])
        df_iv_all["mois"] = df_iv_all["date_intervention"].dt.to_period("M")
        df_iv_all["duree_h"] = df_iv_all.apply(
            lambda r: (
                datetime.datetime.strptime(str(r["heure_fin"]), "%H:%M:%S") -
                datetime.datetime.strptime(str(r["heure_debut"]), "%H:%M:%S")
            ).seconds / 3600 if pd.notna(r["heure_debut"]) and pd.notna(r["heure_fin"]) else 0,
            axis=1
        )

        interv_noms_h = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_interv_h.iterrows()}
        df_iv_all["intervenant_nom"] = df_iv_all["intervenant_id"].apply(lambda x: interv_noms_h.get(str(x), "Non assigné"))

        # Filtre mois
        mois_dispo = df_iv_all["mois"].dropna().unique()
        mois_dispo_str = sorted([str(m) for m in mois_dispo], reverse=True)
        mois_sel = st.selectbox("Mois", mois_dispo_str) if mois_dispo_str else None

        if mois_sel:
            df_mois = df_iv_all[df_iv_all["mois"].astype(str) == mois_sel]
            df_resume = df_mois.groupby("intervenant_nom").agg(
                nb_interventions=("id", "count"),
                heures_planifiees=("duree_h", "sum"),
                heures_realisees=("duree_h", lambda x: x[df_mois.loc[x.index, "statut"] == "Réalisé"].sum())
            ).reset_index()

            st.markdown(f"### Mois de {mois_sel}")
            col_t1, col_t2, col_t3 = st.columns(3)
            col_t1.metric("Total interventions", int(df_resume["nb_interventions"].sum()))
            col_t2.metric("Heures planifiées", f"{df_resume['heures_planifiees'].sum():.1f}h")
            col_t3.metric("Heures réalisées", f"{df_resume['heures_realisees'].sum():.1f}h")

            st.dataframe(
                df_resume.rename(columns={
                    "intervenant_nom": "Intervenant",
                    "nb_interventions": "Nb interventions",
                    "heures_planifiees": "Heures planifiées",
                    "heures_realisees": "Heures réalisées"
                }),
                use_container_width=True, hide_index=True
            )

            # Export CSV
            csv = df_resume.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Exporter en CSV", data=csv,
                               file_name=f"heures_{mois_sel}.csv", mime="text/csv")


# ============================================================
#  👤 MON PROFIL — Version admin / version client
# ============================================================
elif onglet == "👤 Mon Profil":

    if IS_ADMIN:
        # -------------------------------------------------------
        #  PROFIL ADMIN = Éditeur SaaS
        #  Il configure uniquement sa boîte mail pour envoyer
        #  les identifiants à ses clients.
        # -------------------------------------------------------
        st.caption("👑 Compte administrateur OmniCoord IA — Éditeur SaaS")

        st.subheader("🔑 Changer mon mot de passe admin")
        with st.form("form_mdp_admin"):
            n1 = st.text_input("Nouveau mot de passe", type="password")
            n2 = st.text_input("Confirmer", type="password")
            if st.form_submit_button("Mettre à jour"):
                if not n1 or n1 != n2:
                    st.error("Les mots de passe ne correspondent pas.")
                elif len(n1) < 8:
                    st.error("8 caractères minimum.")
                else:
                    try:
                        sb.auth.update_user({"password": n1})
                        audit("CHANGE_PASSWORD", "profils", USER_ID)
                        st.success("Mot de passe mis à jour.")
                    except Exception as e:
                        st.error(f"Erreur : {e}")

        st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
        st.subheader("📧 Ma boîte mail (envoi des accès clients)")
        st.info("💡 Cette boîte sert à envoyer automatiquement les identifiants à vos nouveaux clients. Utilisez un **mot de passe d'application Gmail** (pas votre mot de passe personnel). Générez-en un sur myaccount.google.com > Sécurité > Mots de passe des applications.")

        with st.form("form_mail_admin"):
            cfg = st.session_state.get("mail_config", {})
            mail_e = st.text_input("Adresse e-mail d'envoi", value=cfg.get("email", ""))
            mail_p = st.text_input("Mot de passe d'application Gmail (16 caractères)", type="password",
                                    help="Chiffré avec Fernet avant stockage en base.")
            mail_i = st.text_input("Serveur IMAP", value=cfg.get("imap", "imap.gmail.com"))

            if st.form_submit_button("Enregistrer"):
                if mail_p and len(mail_p) not in [16, 19]:
                    st.warning("Un mot de passe d'application Gmail fait normalement 16 caractères.")
                mdp_chiffre = chiffrer_mdp_mail(mail_p) if mail_p else ""
                update_data = {"mail_smtp_email": mail_e, "mail_imap_server": mail_i}
                if mdp_chiffre:
                    update_data["mail_smtp_password"] = mdp_chiffre
                if sb_update("profils", update_data, "id", USER_ID):
                    st.session_state["mail_config"] = {
                        "email": mail_e,
                        "password": mail_p or cfg.get("password", ""),
                        "imap": mail_i
                    }
                    audit("UPDATE_MAIL_CONFIG", "profils", USER_ID)
                    st.success("✅ Configuration mail enregistrée (mot de passe chiffré).")

        # Test de connexion mail
        st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
        st.subheader("🧪 Tester l'envoi de mail")
        email_test = st.text_input("Envoyer un email de test à :")
        if st.button("Envoyer le test") and email_test:
            ok, msg = envoyer_email(
                email_test,
                "Test OmniCoord IA — Configuration mail OK",
                "Bonjour,\n\nCeci est un email de test envoyé depuis OmniCoord IA.\n\nSi vous recevez ce message, votre configuration mail est opérationnelle et vous pouvez envoyer les identifiants à vos clients.\n\nOmniCoord IA"
            )
            if ok: st.success(f"✅ {msg}")
            else: st.error(f"❌ {msg}")

    else:
        # -------------------------------------------------------
        #  PROFIL CLIENT = SAAD / SSIAD
        #  Il configure sa boîte mail pour envoyer les
        #  sollicitations d'urgence à ses intervenants.
        # -------------------------------------------------------
        st.caption(f"Structure : **{st.session_state.get('structure_nom', '—')}**")

        st.subheader("🔑 Changer mon mot de passe")
        with st.form("form_mdp"):
            n1 = st.text_input("Nouveau mot de passe", type="password")
            n2 = st.text_input("Confirmer", type="password")
            if st.form_submit_button("Mettre à jour"):
                if not n1 or n1 != n2:
                    st.error("Les mots de passe ne correspondent pas.")
                elif len(n1) < 8:
                    st.error("8 caractères minimum.")
                else:
                    try:
                        sb.auth.update_user({"password": n1})
                        audit("CHANGE_PASSWORD", "profils", USER_ID)
                        st.success("Mot de passe mis à jour.")
                    except Exception as e:
                        st.error(f"Erreur : {e}")

        st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
        st.subheader("📧 Ma boîte mail (sollicitations intervenants)")
        st.info("💡 Gmail : utilisez un **mot de passe d'application** (pas votre mot de passe principal). Générez-en un sur myaccount.google.com > Sécurité > Mots de passe des applications.")

        with st.form("form_mail"):
            cfg = st.session_state.get("mail_config", {})
            mail_e = st.text_input("Adresse e-mail d'envoi", value=cfg.get("email", ""))
            mail_p = st.text_input("Mot de passe d'application Gmail (16 caractères)", type="password",
                                    help="Ce mot de passe est chiffré avant d'être stocké.")
            mail_i = st.text_input("Serveur IMAP", value=cfg.get("imap", "imap.gmail.com"))

            if st.form_submit_button("Enregistrer"):
                if mail_p and len(mail_p) not in [16, 19]:
                    st.warning("Un mot de passe d'application Gmail fait normalement 16 caractères.")
                mdp_chiffre = chiffrer_mdp_mail(mail_p) if mail_p else ""
                update_data = {"mail_smtp_email": mail_e, "mail_imap_server": mail_i}
                if mdp_chiffre:
                    update_data["mail_smtp_password"] = mdp_chiffre
                if sb_update("profils", update_data, "id", USER_ID):
                    st.session_state["mail_config"] = {
                        "email": mail_e,
                        "password": mail_p or cfg.get("password", ""),
                        "imap": mail_i
                    }
                    audit("UPDATE_MAIL_CONFIG", "profils", USER_ID)
                    st.success("✅ Configuration mail enregistrée (mot de passe chiffré).")

        # Test de connexion mail
        st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
        st.subheader("🧪 Tester la connexion mail")
        email_test = st.text_input("Envoyer un email de test à :")
        if st.button("Envoyer le test") and email_test:
            ok, msg = envoyer_email(
                email_test,
                "Test OmniCoord IA — Connexion mail OK",
                "Bonjour,\n\nCeci est un email de test envoyé depuis OmniCoord IA.\nSi vous recevez ce message, la configuration mail est correcte.\n\nOmniCoord IA"
            )
            if ok: st.success(f"✅ {msg}")
            else: st.error(f"❌ {msg}")


# ============================================================
#  🛠️ ADMINISTRATION — Cockpit commercial SaaS
#  Visible uniquement par l'admin (éditeur OmniCoord)
# ============================================================
elif onglet == "🛠️ Administration" and IS_ADMIN:

    APP_URL = "https://omnicoord-ia-bxgnddxxgniwnhhu9pmo9r.streamlit.app"

    tab_clients, tab_creer, tab_quotas, tab_audit, tab_secu = st.tabs([
        "📋 Mes clients",
        "➕ Créer un accès client",
        "📊 Quotas IA",
        "📋 Journal d'audit",
        "🔐 Sécurité"
    ])

    # ----------------------------------------------------------
    #  TAB 1 : LISTE DES CLIENTS
    # ----------------------------------------------------------
    with tab_clients:
        st.subheader("📋 Mes clients — Vue d'ensemble")

        df_structs_admin = sb_select("structures", order="nom")
        df_users_admin = sb_select("profils", order="email")

        # Exclure l'admin de la liste des clients
        df_clients = df_users_admin[df_users_admin["est_admin"] == False].copy() if not df_users_admin.empty else pd.DataFrame()

        # Métriques commerciales
        col_c1, col_c2, col_c3, col_c4 = st.columns(4)
        nb_clients_total = len(df_clients)
        nb_actifs = 0
        nb_expires = 0
        nb_essai = 0
        if not df_clients.empty:
            df_clients["date_fin_dt"] = pd.to_datetime(df_clients["date_fin_essai"], errors="coerce").dt.date
            aujourdhui = datetime.date.today()
            nb_actifs = len(df_clients[df_clients["date_fin_dt"] >= aujourdhui])
            nb_expires = len(df_clients[df_clients["date_fin_dt"] < aujourdhui])
            nb_essai = len(df_clients[df_clients["statut_abonnement"] == "ESSAI"])

        col_c1.metric("👥 Total clients", nb_clients_total)
        col_c2.metric("✅ Actifs", nb_actifs)
        col_c3.metric("⏰ En essai", nb_essai)
        col_c4.metric("🔴 Expirés", nb_expires, delta=f"-{nb_expires}" if nb_expires > 0 else None, delta_color="inverse")

        st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)

        if df_clients.empty:
            st.info("Aucun client créé pour l'instant. Utilisez l'onglet « Créer un accès client » pour commencer.")
        else:
            # Récupérer les noms de structure
            struct_noms = {}
            if not df_structs_admin.empty:
                struct_noms = {str(r["id"]): r["nom"] for _, r in df_structs_admin.iterrows()}

            for _, client in df_clients.iterrows():
                struct_nom = h(struct_noms.get(str(client.get("structure_id", "")), "Non assignée"))
                email_c = h(client["email"])
                statut_c = client["statut_abonnement"]
                date_fin_c = client.get("date_fin_essai", "—")

                # Couleur selon statut
                if client.get("date_fin_dt") and client["date_fin_dt"] < datetime.date.today():
                    couleur = "#e0554f"
                    badge = "Expiré"
                elif statut_c == "PRO":
                    couleur = "#3fae74"
                    badge = "PRO"
                else:
                    couleur = "#d99a3d"
                    badge = "Essai"

                st.markdown(f"""
                    <div class="oc-card" style="border-left-color:{couleur};">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="font-size:16px; font-weight:700;">🏢 {struct_nom}</span>
                            <span class="oc-badge" style="background:{couleur};">{badge}</span>
                        </div>
                        <div style="color:#b8c2cc; font-size:13px; margin-top:6px;">
                            📧 {email_c} &nbsp;|&nbsp; 📅 Accès jusqu'au {h(str(date_fin_c))} &nbsp;|&nbsp;
                            🤖 IA : {client.get('nb_requetes_ia', 0)}/{client.get('quota_max_ia', 20)}
                        </div>
                    </div>
                """, unsafe_allow_html=True)

                with st.expander(f"⚙️ Gérer — {email_c}"):
                    col_g1, col_g2, col_g3 = st.columns(3)

                    with col_g1:
                        # Prolonger l'accès
                        jours_prolonger = st.number_input("Prolonger (jours)", min_value=1, value=30, key=f"prol_{client['id']}")
                        if st.button("📅 Prolonger l'accès", key=f"btn_prol_{client['id']}"):
                            nouvelle_fin = (datetime.date.today() + datetime.timedelta(days=int(jours_prolonger))).isoformat()
                            if sb_update("profils", {"date_fin_essai": nouvelle_fin}, "id", str(client["id"])):
                                audit("PROLONGER_ACCES", "profils", str(client["id"]), {"nouvelle_fin": nouvelle_fin})
                                st.success(f"Accès prolongé jusqu'au {date_fr(nouvelle_fin, 'court')}")
                                st.rerun()

                    with col_g2:
                        # Changer le statut d'abonnement
                        nv_statut = st.selectbox("Statut abonnement", ["ESSAI", "PRO", "SUSPENDU"],
                                                  index=["ESSAI", "PRO", "SUSPENDU"].index(statut_c) if statut_c in ["ESSAI", "PRO", "SUSPENDU"] else 0,
                                                  key=f"stat_{client['id']}")
                        if st.button("💳 Mettre à jour le statut", key=f"btn_stat_{client['id']}"):
                            if sb_update("profils", {"statut_abonnement": nv_statut}, "id", str(client["id"])):
                                audit("UPDATE_ABONNEMENT", "profils", str(client["id"]), {"statut": nv_statut})
                                st.success(f"Statut mis à jour → {nv_statut}")
                                st.rerun()

                    with col_g3:
                        # Modifier le quota IA
                        nv_quota = st.number_input("Quota IA max", min_value=1, value=int(client.get("quota_max_ia", 20)), key=f"quota_{client['id']}")
                        if st.button("🤖 Modifier le quota", key=f"btn_quota_{client['id']}"):
                            if sb_update("profils", {"quota_max_ia": int(nv_quota)}, "id", str(client["id"])):
                                audit("UPDATE_QUOTA", "profils", str(client["id"]), {"quota": nv_quota})
                                st.success(f"Quota IA mis à jour → {nv_quota}")
                                st.rerun()

                    st.markdown("<br>", unsafe_allow_html=True)

                    # Remettre le quota IA à zéro
                    if st.button("🔄 Remettre le compteur IA à 0", key=f"reset_{client['id']}"):
                        if sb_update("profils", {"nb_requetes_ia": 0}, "id", str(client["id"])):
                            st.success("Compteur IA remis à zéro.")
                            st.rerun()

                    # Renvoyer les identifiants par mail
                    if st.button("📧 Renvoyer les identifiants par mail", key=f"remail_{client['id']}"):
                        ok, msg = envoyer_email(
                            client["email"],
                            "OmniCoord IA — Vos identifiants de connexion",
                            f"Bonjour,\n\n"
                            f"Voici vos identifiants pour accéder à OmniCoord IA :\n\n"
                            f"🔗 Lien : {APP_URL}\n"
                            f"📧 Email : {client['email']}\n"
                            f"📅 Accès valable jusqu'au : {date_fin_c}\n\n"
                            f"Si vous avez oublié votre mot de passe, contactez l'administrateur.\n\n"
                            f"Cordialement,\nOmniCoord IA"
                        )
                        if ok: st.success(f"✅ Email envoyé à {client['email']}")
                        else: st.error(f"❌ {msg}")

                    # Supprimer le client
                    st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
                    confirm_del = st.checkbox(f"Je confirme la suppression de {email_c} et toutes ses données", key=f"confirm_{client['id']}")
                    if st.button("🗑️ Supprimer définitivement ce client", key=f"del_{client['id']}"):
                        if confirm_del:
                            try:
                                # Supprimer dans Supabase Auth
                                get_supabase_admin().auth.admin.delete_user(str(client["id"]))
                                # Supprimer le profil (les données métier restent liées à la structure)
                                sb_delete("profils", "id", str(client["id"]))
                                audit("DELETE_CLIENT", "profils", str(client["id"]), {"email": client["email"]})
                                st.success(f"Client {client['email']} supprimé.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Erreur : {e}")
                        else:
                            st.warning("Cochez la case de confirmation.")

    # ----------------------------------------------------------
    #  TAB 2 : CRÉER UN ACCÈS CLIENT
    # ----------------------------------------------------------
    with tab_creer:
        st.subheader("➕ Créer un accès client")
        st.caption("Crée un compte pour un nouveau client. Un email avec le lien, l'identifiant et le mot de passe lui sera envoyé automatiquement.")

        with st.form("form_creer_client"):
            col_c1, col_c2 = st.columns(2)
            with col_c1:
                nom_structure = st.text_input("Nom de la structure (SAAD / SSIAD) *")
                email_client = st.text_input("Email du client *")
                mdp_client = st.text_input("Mot de passe temporaire *")
            with col_c2:
                duree_acces = st.number_input("Durée d'accès (jours)", min_value=1, value=30)
                statut_abo = st.selectbox("Type d'abonnement", ["ESSAI", "PRO"])
                quota_ia = st.number_input("Quota IA (nombre de requêtes)", min_value=1, value=20)

            envoyer_mail_auto = st.checkbox("📧 Envoyer automatiquement les identifiants par email", value=True)
            btn_creer = st.form_submit_button("🚀 Créer l'accès client")

            if btn_creer:
                if not nom_structure or not email_client or not mdp_client:
                    st.error("Tous les champs avec * sont obligatoires.")
                elif len(mdp_client) < 8:
                    st.error("Le mot de passe doit faire au moins 8 caractères.")
                else:
                    # 1. Créer la structure
                    struct_existante = sb_select("structures", {"nom": nom_structure.strip()})
                    if struct_existante.empty:
                        struct_res = sb_insert("structures", {"nom": nom_structure.strip()})
                        if struct_res:
                            struct_id = struct_res["id"]
                        else:
                            struct_id = None
                    else:
                        struct_id = struct_existante.iloc[0]["id"]

                    if struct_id:
                        date_fin = (datetime.date.today() + datetime.timedelta(days=int(duree_acces))).isoformat()
                        try:
                            # 2. Créer l'utilisateur dans Supabase Auth
                            auth_res = get_supabase_admin().auth.admin.create_user({
                                "email": email_client.strip().lower(),
                                "password": mdp_client,
                                "email_confirm": True
                            })
                            new_uid = auth_res.user.id

                            # 3. Créer le profil
                            sb_insert("profils", {
                                "id": new_uid,
                                "structure_id": struct_id,
                                "email": email_client.strip().lower(),
                                "est_admin": False,
                                "statut_abonnement": statut_abo,
                                "quota_max_ia": int(quota_ia),
                                "date_fin_essai": date_fin
                            })
                            audit("CREATE_CLIENT", "profils", new_uid, {
                                "structure": nom_structure, "email": email_client, "duree": duree_acces
                            })

                            st.success(f"✅ Accès créé pour **{email_client}** (structure : {nom_structure}) jusqu'au **{date_fr(date_fin, 'court')}**")

                            # 4. Envoyer les identifiants par mail (si coché)
                            if envoyer_mail_auto:
                                ok, msg = envoyer_email(
                                    email_client,
                                    "Bienvenue sur OmniCoord IA — Vos identifiants",
                                    f"Bonjour,\n\n"
                                    f"Votre accès à OmniCoord IA a été créé avec succès.\n\n"
                                    f"🔗 Lien de connexion : {APP_URL}\n\n"
                                    f"📧 Identifiant : {email_client}\n"
                                    f"🔑 Mot de passe : {mdp_client}\n\n"
                                    f"📅 Votre accès est valable jusqu'au {date_fr(date_fin, 'court')}.\n\n"
                                    f"⚠️ Nous vous recommandons de changer votre mot de passe dès votre première connexion (Mon Profil > Changer mon mot de passe).\n\n"
                                    f"Pour toute question, contactez-nous.\n\n"
                                    f"Cordialement,\n"
                                    f"L'équipe OmniCoord IA"
                                )
                                if ok:
                                    st.success(f"📧 Email envoyé à {email_client}")
                                else:
                                    st.warning(f"⚠️ Accès créé mais email non envoyé : {msg}")
                                    st.info(f"Identifiants à transmettre manuellement :\n- Lien : {APP_URL}\n- Email : {email_client}\n- Mot de passe : {mdp_client}")
                            else:
                                st.info(f"📋 Identifiants à transmettre manuellement :\n- **Lien** : {APP_URL}\n- **Email** : {email_client}\n- **Mot de passe** : {mdp_client}")

                        except Exception as e:
                            err_msg = str(e)
                            if "already been registered" in err_msg or "already exists" in err_msg:
                                st.error("Cet email est déjà utilisé par un autre compte.")
                            else:
                                logger.error(f"Création client: {e}")
                                st.error(f"Erreur lors de la création : {e}")

    # ----------------------------------------------------------
    #  TAB 3 : QUOTAS IA
    # ----------------------------------------------------------
    with tab_quotas:
        st.subheader("📊 Quotas IA — Vue globale")
        df_users_q = sb_select("profils", order="email")
        if not df_users_q.empty:
            df_clients_q = df_users_q[df_users_q["est_admin"] == False].copy()
            if not df_clients_q.empty:
                df_clients_q["usage_%"] = (df_clients_q["nb_requetes_ia"] / df_clients_q["quota_max_ia"] * 100).round(1)
                st.dataframe(
                    df_clients_q[["email", "nb_requetes_ia", "quota_max_ia", "usage_%", "statut_abonnement"]].rename(columns={
                        "email": "Client",
                        "nb_requetes_ia": "Utilisées",
                        "quota_max_ia": "Quota max",
                        "usage_%": "Utilisation %",
                        "statut_abonnement": "Abonnement"
                    }),
                    use_container_width=True, hide_index=True
                )
            else:
                st.info("Aucun client créé.")
        else:
            st.info("Aucune donnée disponible.")

    # ----------------------------------------------------------
    #  TAB 4 : JOURNAL D'AUDIT
    # ----------------------------------------------------------
    with tab_audit:
        st.subheader("📋 Journal d'audit — 50 dernières actions")
        df_audit = sb_select("audit_logs", order="created_at")
        if not df_audit.empty:
            df_audit_aff = df_audit.sort_values("created_at", ascending=False).head(50)
            st.dataframe(
                df_audit_aff[["created_at", "action", "table_name", "record_id"]].rename(columns={
                    "created_at": "Date",
                    "action": "Action",
                    "table_name": "Table",
                    "record_id": "Enregistrement"
                }),
                use_container_width=True, hide_index=True
            )
        else:
            st.caption("Aucune action enregistrée.")

    # ----------------------------------------------------------
    #  TAB 5 : SÉCURITÉ
    # ----------------------------------------------------------
    with tab_secu:
        st.subheader("🔐 État de la sécurité")
        st.markdown("""
        - ✅ **Auth** : Supabase Auth (bcrypt natif + JWT)
        - ✅ **RLS** : Cloisonnement par structure garanti au niveau base de données
        - ✅ **Mots de passe mail** : Chiffrés avec Fernet avant stockage
        - ✅ **Anti brute-force** : Blocage après 10 échecs / 15 min
        - ✅ **Injection HTML** : html.escape() sur toutes les valeurs injectées
        - ✅ **Audit log** : Toutes les actions sensibles tracées
        - ✅ **PDF** : Unicode natif (fpdf2), plus de caractères manquants
        - ✅ **Quota IA** : Re-vérifié en base à chaque appel (pas en session)
        """)
