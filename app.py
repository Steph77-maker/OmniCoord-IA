import datetime
import email
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import imaplib
import json
import os
import re
import smtplib
import sqlite3
import time
import math
import urllib.parse
import bcrypt
from fpdf import FPDF
import google.generativeai as genai
import pandas as pd
from pypdf import PdfReader
import streamlit as st

# ============================================================
#  OMNICOORD IA — Coordination, plannings & sourcing direct
#  pour les structures d'aide à domicile (SAAD / SSIAD)
# ============================================================

DB_NAME = "omnicoord.db"

# ============================================================
#  LOCALISATION FRANÇAISE DES DATES (sans dépendance externe)
# ============================================================
_JOURS_FR = {
    "Monday": "Lundi", "Tuesday": "Mardi", "Wednesday": "Mercredi",
    "Thursday": "Jeudi", "Friday": "Vendredi", "Saturday": "Samedi", "Sunday": "Dimanche",
}
_JOURS_FR_COURT = {
    "Mon": "Lun", "Tue": "Mar", "Wed": "Mer",
    "Thu": "Jeu", "Fri": "Ven", "Sat": "Sam", "Sun": "Dim",
}
_MOIS_FR = {
    "January": "janvier", "February": "février", "March": "mars",
    "April": "avril", "May": "mai", "June": "juin",
    "July": "juillet", "August": "août", "September": "septembre",
    "October": "octobre", "November": "novembre", "December": "décembre",
}


def date_fr(d, format_affichage="long"):
    """Retourne une date formatée en français sans bibliothèque externe.

    Formats disponibles :
      - "long"   → "Vendredi 21 août 2026"
      - "court"  → "21/08/2026"
      - "medium" → "21 août 2026"
      - "semaine"→ "lun. 21/08"
    """
    if isinstance(d, str):
        try:
            d = datetime.date.fromisoformat(d)
        except ValueError:
            return d  # retourne la chaîne telle quelle si non parseable

    if format_affichage == "long":
        jour_en = d.strftime("%A")
        mois_en = d.strftime("%B")
        return f"{_JOURS_FR.get(jour_en, jour_en)} {d.day} {_MOIS_FR.get(mois_en, mois_en)} {d.year}"

    if format_affichage == "medium":
        mois_en = d.strftime("%B")
        return f"{d.day} {_MOIS_FR.get(mois_en, mois_en)} {d.year}"

    if format_affichage == "court":
        return d.strftime("%d/%m/%Y")

    if format_affichage == "semaine":
        jour_en = d.strftime("%a")
        return f"{_JOURS_FR_COURT.get(jour_en, jour_en)}. {d.strftime('%d/%m')}"

    return d.strftime("%d/%m/%Y")


# --- CONFIGURATION DU THÈME VISUEL (DOIT ÊTRE AU TOUT DÉBUT) ---
st.set_page_config(
    page_title="OmniCoord IA",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- CHARTE GRAPHIQUE : BLEU MÉDICAL / NUIT PROFOND + ACIER BROSSÉ ---
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

    .stApp {
        background: linear-gradient(160deg, var(--oc-navy-deep) 0%, var(--oc-navy) 55%, #0d2138 100%);
        color: #f2f5f8 !important;
    }

    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0c1f33 0%, #0a1929 100%);
        border-right: 1px solid rgba(137, 150, 163, 0.25);
    }

    p, span, label, .stMarkdown, div[data-baseweb="select"] span {
        color: #f2f5f8 !important;
    }

    h1, h2, h3, h4, h5, h6 {
        color: #ffffff !important;
        letter-spacing: 0.3px;
    }

    .oc-badge {
        display: inline-block;
        padding: 4px 14px;
        border-radius: 20px;
        font-weight: 700;
        color: white;
    }

    .oc-card {
        background: linear-gradient(135deg, var(--oc-navy-panel) 0%, #0f2438 100%);
        border: 1px solid rgba(137, 150, 163, 0.25);
        border-left: 4px solid var(--oc-medical-blue);
        border-radius: 12px;
        padding: 18px 20px;
        margin-bottom: 14px;
        color: #f2f5f8 !important;
    }

    .oc-card-alert { border-left: 4px solid var(--oc-alert) !important; }
    .oc-card-warning { border-left: 4px solid var(--oc-warning) !important; }
    .oc-card-ok { border-left: 4px solid var(--oc-success) !important; }

    .oc-metal-divider {
        height: 2px;
        background: linear-gradient(90deg, transparent, var(--oc-steel) 50%, transparent);
        margin: 18px 0;
        opacity: 0.5;
    }

    .stButton > button {
        background: linear-gradient(135deg, var(--oc-medical-blue) 0%, #1f5fd6 100%);
        color: white;
        border: none;
        border-radius: 8px;
        font-weight: 600;
    }

    .stButton > button:hover {
        background: linear-gradient(135deg, var(--oc-medical-blue-soft) 0%, var(--oc-medical-blue) 100%);
        border: none;
        color: white;
    }

    div[data-testid="stMetricValue"] {
        color: var(--oc-medical-blue-soft) !important;
    }

    input, textarea, select {
        color: #ffffff !important;
    }

    div[data-baseweb="input"] {
        background-color: rgba(19, 47, 76, 0.6) !important;
        color: #ffffff !important;
    }

    div[data-testid="stTextInput"] label p,
    div[data-testid="stPasswordInput"] label p,
    .stTextInput label,
    .stPasswordInput label {
        color: #f2f5f8 !important;
        font-weight: 600 !important;
    }

    div[data-baseweb="base-input"] input {
        color: #ffffff !important;
        background-color: rgba(13, 33, 56, 0.8) !important;
    }

    /* Planning hebdomadaire */
    .planning-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 13px;
        margin-top: 10px;
    }
    .planning-table th {
        background: linear-gradient(135deg, #1a3f6f 0%, #0f2942 100%);
        color: #f2f5f8;
        padding: 10px 8px;
        text-align: center;
        border: 1px solid rgba(137,150,163,0.3);
        font-weight: 700;
        letter-spacing: 0.5px;
    }
    .planning-table th.col-intervenant {
        background: linear-gradient(135deg, #0c1f33 0%, #0a1929 100%);
        text-align: left;
        padding-left: 12px;
        min-width: 140px;
    }
    .planning-table td {
        border: 1px solid rgba(137,150,163,0.2);
        padding: 6px 4px;
        vertical-align: top;
        min-width: 110px;
        min-height: 50px;
        background: rgba(10, 25, 41, 0.4);
    }
    .planning-table td.col-intervenant {
        background: rgba(12, 31, 51, 0.7);
        color: #e6ecf2;
        font-weight: 600;
        padding: 8px 12px;
        vertical-align: middle;
    }
    .planning-cell {
        background: linear-gradient(135deg, #132f4c 0%, #0f2438 100%);
        border-radius: 6px;
        padding: 5px 7px;
        margin: 2px;
        font-size: 12px;
        border-left: 3px solid #2f7cf6;
        color: #f2f5f8;
    }
    .planning-cell.urgence { border-left-color: #e0554f !important; }
    .planning-cell.realise { border-left-color: #3fae74 !important; }
    .planning-cell.annule { border-left-color: #8996a3 !important; opacity: 0.6; }
    .planning-empty { color: rgba(137,150,163,0.3); font-size: 12px; text-align: center; padding: 10px 0; }

    /* Alertes dashboard */
    .alert-box {
        border-radius: 10px;
        padding: 14px 18px;
        margin-bottom: 10px;
        display: flex;
        align-items: flex-start;
        gap: 12px;
    }
    .alert-box-rouge {
        background: rgba(224, 85, 79, 0.12);
        border: 1px solid rgba(224, 85, 79, 0.45);
        border-left: 4px solid #e0554f;
    }
    .alert-box-orange {
        background: rgba(217, 154, 61, 0.12);
        border: 1px solid rgba(217, 154, 61, 0.40);
        border-left: 4px solid #d99a3d;
    }
    .alert-box-bleu {
        background: rgba(47, 124, 246, 0.10);
        border: 1px solid rgba(47, 124, 246, 0.35);
        border-left: 4px solid #2f7cf6;
    }
    .alert-icon { font-size: 20px; margin-top: 2px; flex-shrink: 0; }
    .alert-content { flex: 1; }
    .alert-title { font-weight: 700; color: #f2f5f8; font-size: 14px; }
    .alert-detail { color: #b8c2cc; font-size: 13px; margin-top: 2px; }

    /* Fiche bénéficiaire */
    .fiche-section {
        background: linear-gradient(135deg, #132f4c 0%, #0f2438 100%);
        border: 1px solid rgba(137,150,163,0.2);
        border-radius: 10px;
        padding: 16px 18px;
        margin-bottom: 12px;
    }
    .fiche-section h4 { color: #4c8dfa !important; margin-bottom: 10px; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; }
    .fiche-row { display: flex; gap: 8px; margin-bottom: 6px; }
    .fiche-label { color: #8996a3; font-size: 13px; min-width: 140px; flex-shrink: 0; }
    .fiche-value { color: #f2f5f8; font-size: 13px; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# --- SÉCURITÉ : HACHAGE DES MOTS DE PASSE (bcrypt) ---
def hacher_mdp(mot_de_passe_clair):
    return bcrypt.hashpw(mot_de_passe_clair.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verifier_mdp(mot_de_passe_saisi, valeur_stockee):
    if not valeur_stockee:
        return False
    try:
        if valeur_stockee.startswith(("$2b$", "$2a$", "$2y$")):
            return bcrypt.checkpw(mot_de_passe_saisi.encode("utf-8"), valeur_stockee.encode("utf-8"))
    except Exception:
        return False
    return mot_de_passe_saisi == valeur_stockee


def mdp_est_hashe(valeur_stockee):
    return bool(valeur_stockee) and valeur_stockee.startswith(("$2b$", "$2a$", "$2y$"))


# --- SÉCURITÉ & QUOTAS IA ---
def peut_utiliser_ia(email_utilisateur):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT nb_requetes_ia, quota_max, statut_abonnement FROM utilisateurs WHERE email = ?", (email_utilisateur,))
        row = c.fetchone()
        conn.close()
        if not row:
            return False, 0, 0
        nb, quota_max, statut = row
        if statut == "PRO":
            return True, nb, quota_max
        return nb < quota_max, nb, quota_max
    except Exception:
        return False, 0, 0


def incrementer_quota_ia(email_utilisateur):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE utilisateurs SET nb_requetes_ia = nb_requetes_ia + 1 WHERE email = ?", (email_utilisateur,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def reinitialiser_quota_ia(email_utilisateur):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE utilisateurs SET nb_requetes_ia = 0 WHERE email = ?", (email_utilisateur,))
        conn.commit()
        conn.close()
    except Exception:
        pass


# --- GÉNÉRATION PDF (UTF-8 compatible) ---
class PDFDocument(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(15, 41, 66)
        self.cell(0, 10, "OmniCoord IA", ln=True, align="L")
        self.set_draw_color(47, 124, 246)
        self.line(10, 20, 200, 20)
        self.ln(6)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(137, 150, 163)
        self.cell(0, 10, f"Document généré le {datetime.date.today().strftime('%d/%m/%Y')} — OmniCoord IA", align="C")


def creer_pdf_transmission(beneficiaire_nom, intervenant_nom, date_doc, contenu):
    pdf = PDFDocument()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 8, f"Fiche de liaison / Transmission — {date_doc}", ln=True)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, f"Bénéficiaire : {beneficiaire_nom}", ln=True)
    pdf.cell(0, 8, f"Intervenant : {intervenant_nom}", ln=True)
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 10)
    contenu_safe = contenu.encode("latin-1", "replace").decode("latin-1")
    pdf.multi_cell(0, 6, contenu_safe)
    return pdf.output(dest="S")


def creer_pdf_fiche_intervenant(nom, competences, habilitations, details):
    pdf = PDFDocument()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, f"Fiche intervenant — {nom}", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.ln(2)
    pdf.multi_cell(0, 6, f"Compétences : {competences}".encode("latin-1", "replace").decode("latin-1"))
    pdf.multi_cell(0, 6, f"Habilitations : {habilitations}".encode("latin-1", "replace").decode("latin-1"))
    pdf.ln(2)
    pdf.multi_cell(0, 6, details.encode("latin-1", "replace").decode("latin-1"))
    return pdf.output(dest="S")


# --- ENVOI D'EMAIL ---
def envoyer_email_intervenant(to_email, sujet, corps_message, email_user, pwd_user, smtp_server="smtp.gmail.com", smtp_port=587):
    try:
        msg = MIMEMultipart()
        msg["From"] = email_user
        msg["To"] = to_email
        msg["Subject"] = sujet
        msg.attach(MIMEText(corps_message, "plain"))
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(email_user, pwd_user)
        server.sendmail(email_user, to_email, msg.as_string())
        server.quit()
        return True, "Email envoyé avec succès."
    except Exception as e:
        return False, f"Erreur d'envoi : {e}"


# ============================================================
#  AGENT IA — AUTOMATISATION DES REMPLACEMENTS D'URGENCE
# ============================================================
def classer_candidats_urgence(urg_row, df_dispo, structure_id):
    resultats = []
    for _, interv in df_dispo.iterrows():
        df_habs = charger_df(
            "SELECT * FROM habilitations WHERE intervenant_id = ? AND structure_id = ?",
            (int(interv["id"]), structure_id)
        )
        habs_txt = "; ".join(
            [f"{h['type_habilitation']} (exp. {h['date_expiration']})" for _, h in df_habs.iterrows()]
        ) or "Aucune habilitation enregistrée"

        prompt = f"""
        Tu es un coordinateur expert en aide à domicile (SAAD/SSIAD) chargé de trouver en urgence
        un remplaçant pour une intervention non pourvue.

        CONSIGNES :
        1. Évalue la pertinence de ce candidat pour CE remplacement précis, en te basant sur :
           - la couverture des habilitations nécessaires au type d'intervention ;
           - la proximité géographique déclarée (zone_geo) avec le secteur du bénéficiaire ;
           - la compatibilité de ses disponibilités déclarées avec le créneau à pourvoir.
        2. N'invente jamais une donnée absente.

        Renvoie STRICTEMENT un objet JSON avec les clés :
        - 'score_global': entier 0-100
        - 'alerte_habilitation': texte court si une habilitation obligatoire semble manquante, sinon chaîne vide
        - 'justification': synthèse en une phrase

        INTERVENTION À POURVOIR :
        Date/heure : {urg_row['date_intervention']} de {urg_row['heure_debut']} à {urg_row['heure_fin']}
        Type d'intervention : {urg_row['type_intervention']}
        Bénéficiaire — besoins : {urg_row.get('gestes_techniques', '') or 'Non renseigné'}

        PROFIL INTERVENANT CANDIDAT :
        Compétences déclarées : {interv['competences']}
        Zone géographique : {interv['zone_geo']}
        Disponibilités déclarées : {interv['disponibilites']}
        Habilitations : {habs_txt}
        """
        try:
            reponse = model.generate_content(prompt)
            txt = reponse.text.strip().replace("```json", "").replace("```", "").strip()
            data = json.loads(txt)
        except Exception:
            data = {"score_global": 0, "alerte_habilitation": "", "justification": "Évaluation IA indisponible pour ce candidat."}

        data["intervenant_id"] = int(interv["id"])
        data["intervenant_nom"] = f"{interv['prenom']} {interv['nom']}"
        data["intervenant_email"] = interv["email"]
        data["intervenant_zone"] = interv["zone_geo"]
        resultats.append(data)

    return sorted(resultats, key=lambda x: int(x.get("score_global", 0)), reverse=True)


def prochain_candidat_non_sollicite(intervention_id, classement, structure_id):
    df_deja = charger_df(
        "SELECT intervenant_id FROM sollicitations_urgence WHERE intervention_id = ? AND structure_id = ? AND statut != 'Accepté'",
        (intervention_id, structure_id)
    )
    ids_exclus = set(df_deja["intervenant_id"].tolist()) if not df_deja.empty else set()
    for candidat in classement:
        if candidat["intervenant_id"] not in ids_exclus:
            return candidat
    return None


def solliciter_candidat_urgence(urg_row, candidat, structure_id):
    cfg = st.session_state.get("mail_config", {})
    if not candidat.get("intervenant_email") or not cfg.get("email"):
        return False, "Impossible d'envoyer : email du candidat ou boîte mail de la structure non configurés."

    sujet = f"Remplacement urgent le {urg_row['date_intervention']}"
    corps = (
        f"Bonjour,\n\n"
        f"Une intervention est à pourvoir en urgence le {urg_row['date_intervention']} "
        f"de {urg_row['heure_debut']} à {urg_row['heure_fin']} ({urg_row['type_intervention']}).\n"
        f"Merci de nous confirmer votre disponibilité au plus vite en répondant à ce message.\n\nMerci."
    )
    ok, msg = envoyer_email_intervenant(candidat["intervenant_email"], sujet, corps, cfg["email"], cfg["password"])
    if ok:
        executer(
            """INSERT INTO sollicitations_urgence
               (structure_id, intervention_id, intervenant_id, score_global, justification, alerte_habilitation, date_envoi, statut)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'En attente')""",
            (structure_id, int(urg_row["id"]), candidat["intervenant_id"], int(candidat.get("score_global", 0)),
             candidat.get("justification", ""), candidat.get("alerte_habilitation", ""), datetime.datetime.now().isoformat())
        )
    return ok, msg


def traiter_reponse_sollicitation(sollicitation_id, intervention_id, intervenant_id, reponse, structure_id):
    executer(
        "UPDATE sollicitations_urgence SET statut = ? WHERE id = ? AND structure_id = ?",
        (reponse, sollicitation_id, structure_id)
    )
    if reponse == "Accepté":
        executer(
            "UPDATE interventions SET intervenant_id = ?, statut = 'Planifié' WHERE id = ? AND structure_id = ?",
            (intervenant_id, intervention_id, structure_id)
        )


# --- CALCUL DE PROXIMITÉ ---
def distance_km(lat1, lon1, lat2, lon2):
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
#  AUTHENTIFICATION
# ============================================================
def initialiser_auth_db():
    try:
        conn_auth = sqlite3.connect(DB_NAME)
        c_auth = conn_auth.cursor()
        c_auth.execute("""
            CREATE TABLE IF NOT EXISTS utilisateurs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                password TEXT,
                date_fin_essai TEXT,
                est_admin INTEGER DEFAULT 0,
                mail_perso TEXT,
                mail_password TEXT,
                mail_imap TEXT,
                nb_requetes_ia INTEGER DEFAULT 0,
                quota_max INTEGER DEFAULT 20,
                statut_abonnement TEXT DEFAULT 'ESSAI',
                structure_id INTEGER
            )
        """)
        c_auth.execute("""
            CREATE TABLE IF NOT EXISTS structures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT UNIQUE,
                date_creation TEXT
            )
        """)
        conn_auth.commit()

        c_auth.execute("SELECT COUNT(*) FROM utilisateurs")
        if c_auth.fetchone()[0] == 0:
            mdp_admin_clair = st.secrets.get("APP_PASSWORD")
            if not mdp_admin_clair:
                st.error(
                    "⚠️ Aucun mot de passe admin défini. Ajoutez APP_PASSWORD dans les "
                    "secrets de l'application (Streamlit Cloud > Settings > Secrets) avant "
                    "de continuer."
                )
                conn_auth.close()
                st.stop()

            mdp_admin_hash = hacher_mdp(mdp_admin_clair)
            default_mail = st.secrets.get("EMAIL_USER", "")
            default_pwd = st.secrets.get("EMAIL_PASSWORD", "")
            default_imap = st.secrets.get("EMAIL_IMAP", "imap.gmail.com")

            c_auth.execute(
                "INSERT INTO structures (nom, date_creation) VALUES (?, ?)",
                ("Structure Interne / Démo", datetime.date.today().isoformat())
            )
            structure_admin_id = c_auth.lastrowid

            c_auth.execute(
                """INSERT INTO utilisateurs (email, password, date_fin_essai, est_admin, mail_perso, mail_password, mail_imap, nb_requetes_ia, quota_max, statut_abonnement, structure_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("admin@omnicoord.fr", mdp_admin_hash, "2099-12-31", 1, default_mail, default_pwd, default_imap, 0, 999999, "PRO", structure_admin_id),
            )
            conn_auth.commit()
        conn_auth.close()
    except Exception as e:
        st.error(f"Erreur d'initialisation du système d'authentification : {e}")


def get_or_create_structure(nom_structure):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id FROM structures WHERE nom = ?", (nom_structure,))
    row = c.fetchone()
    if row:
        structure_id = row[0]
    else:
        c.execute("INSERT INTO structures (nom, date_creation) VALUES (?, ?)", (nom_structure, datetime.date.today().isoformat()))
        conn.commit()
        structure_id = c.lastrowid
    conn.close()
    return structure_id


def check_password():
    if st.session_state.get("password_correct", False):
        return True

    st.markdown(
        """
        <div style="text-align:center; margin-top: 60px;">
            <h1 style="color:#f2f5f8;">🩺 OmniCoord IA</h1>
            <p style="color:#8996a3;">Coordination, plannings & sourcing pour l'aide à domicile</p>
        </div>
        """,
        unsafe_allow_html=True
    )

    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        with st.form("form_login"):
            email_saisi = st.text_input("Email")
            pwd_saisi = st.text_input("Mot de passe", type="password")
            submit = st.form_submit_button("Se connecter")

            if submit:
                try:
                    conn = sqlite3.connect(DB_NAME)
                    c = conn.cursor()
                    c.execute(
                        """SELECT password, date_fin_essai, est_admin, mail_perso, mail_password, mail_imap, structure_id
                           FROM utilisateurs WHERE email = ?""",
                        (email_saisi,)
                    )
                    row = c.fetchone()
                    conn.close()

                    if row:
                        db_password, db_date_fin, db_is_admin, m_mail, m_pass, m_imap, db_structure_id = row

                        if verifier_mdp(pwd_saisi, db_password):
                            if not mdp_est_hashe(db_password):
                                try:
                                    conn_mig = sqlite3.connect(DB_NAME)
                                    c_mig = conn_mig.cursor()
                                    c_mig.execute(
                                        "UPDATE utilisateurs SET password = ? WHERE email = ?",
                                        (hacher_mdp(pwd_saisi), email_saisi),
                                    )
                                    conn_mig.commit()
                                    conn_mig.close()
                                except Exception:
                                    pass

                            date_exp = datetime.date.fromisoformat(db_date_fin)
                            aujourdhui = datetime.date.today()

                            if db_is_admin == 1 or aujourdhui <= date_exp:
                                st.session_state["password_correct"] = True
                                st.session_state["user_email"] = email_saisi
                                st.session_state["is_admin"] = bool(db_is_admin)
                                st.session_state["structure_id"] = db_structure_id
                                try:
                                    conn_s = sqlite3.connect(DB_NAME)
                                    row_s = conn_s.execute("SELECT nom FROM structures WHERE id = ?", (db_structure_id,)).fetchone()
                                    conn_s.close()
                                    st.session_state["structure_nom"] = row_s[0] if row_s else "Non assignée"
                                except Exception:
                                    st.session_state["structure_nom"] = "Non assignée"
                                st.session_state["mail_config"] = {
                                    "email": m_mail or "", "password": m_pass or "", "imap": m_imap or "imap.gmail.com"
                                }
                                st.rerun()
                            else:
                                st.error("Votre période d'accès a expiré. Contactez l'administrateur.")
                        else:
                            st.error("Email ou mot de passe incorrect.")
                    else:
                        st.error("Email ou mot de passe incorrect.")
                except Exception as e:
                    st.error(f"Erreur de connexion : {e}")

    return False


initialiser_auth_db()

if not check_password():
    st.stop()

# --- CONFIGURATION IA ---
try:
    gemini_key = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel("gemini-2.0-flash")
    IA_DISPONIBLE = True
except Exception:
    IA_DISPONIBLE = False
    model = None


# ============================================================
#  TABLES MÉTIER
# ============================================================
def initialiser_tables_metier():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS beneficiaires (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            structure_id INTEGER,
            nom TEXT, prenom TEXT, adresse TEXT, telephone TEXT,
            niveau_dependance TEXT,
            pathologies TEXT,
            gestes_techniques TEXT,
            besoins_horaires TEXT,
            referent_famille TEXT,
            notes TEXT,
            statut TEXT DEFAULT 'Actif',
            date_creation TEXT,
            contact_urgence_nom TEXT DEFAULT '',
            contact_urgence_tel TEXT DEFAULT '',
            besoins_recurrents TEXT DEFAULT '',
            intervenant_attitré_id INTEGER DEFAULT NULL
        )
    """)

    # Migration : ajout des colonnes étendues si elles n'existent pas encore
    for col_def in [
        ("contact_urgence_nom", "TEXT DEFAULT ''"),
        ("contact_urgence_tel", "TEXT DEFAULT ''"),
        ("besoins_recurrents", "TEXT DEFAULT ''"),
        ("intervenant_attitré_id", "INTEGER DEFAULT NULL"),
    ]:
        try:
            c.execute(f"ALTER TABLE beneficiaires ADD COLUMN {col_def[0]} {col_def[1]}")
        except Exception:
            pass

    c.execute("""
        CREATE TABLE IF NOT EXISTS intervenants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            structure_id INTEGER,
            nom TEXT, prenom TEXT, telephone TEXT, email TEXT,
            type_statut TEXT,
            competences TEXT,
            experience_texte TEXT,
            zone_geo TEXT,
            disponibilites TEXT,
            statut_dispo TEXT DEFAULT 'Disponible',
            source TEXT,
            date_ajout TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS habilitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            structure_id INTEGER,
            intervenant_id INTEGER,
            type_habilitation TEXT,
            date_obtention TEXT,
            date_expiration TEXT,
            FOREIGN KEY(intervenant_id) REFERENCES intervenants(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS interventions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            structure_id INTEGER,
            beneficiaire_id INTEGER,
            intervenant_id INTEGER,
            date_intervention TEXT,
            heure_debut TEXT,
            heure_fin TEXT,
            type_intervention TEXT,
            statut TEXT DEFAULT 'Planifié',
            notes TEXT,
            FOREIGN KEY(beneficiaire_id) REFERENCES beneficiaires(id),
            FOREIGN KEY(intervenant_id) REFERENCES intervenants(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS documents_transmissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            structure_id INTEGER,
            beneficiaire_id INTEGER,
            intervenant_id INTEGER,
            date_creation TEXT,
            type_document TEXT,
            contenu TEXT,
            FOREIGN KEY(beneficiaire_id) REFERENCES beneficiaires(id),
            FOREIGN KEY(intervenant_id) REFERENCES intervenants(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS sollicitations_urgence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            structure_id INTEGER,
            intervention_id INTEGER,
            intervenant_id INTEGER,
            score_global INTEGER,
            justification TEXT,
            alerte_habilitation TEXT,
            date_envoi TEXT,
            statut TEXT DEFAULT 'En attente',
            FOREIGN KEY(intervention_id) REFERENCES interventions(id),
            FOREIGN KEY(intervenant_id) REFERENCES intervenants(id)
        )
    """)

    conn.commit()
    conn.close()


initialiser_tables_metier()


def charger_df(requete, params=()):
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query(requete, conn, params=params)
    conn.close()
    return df


def executer(requete, params=()):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(requete, params)
    conn.commit()
    last_id = c.lastrowid
    conn.close()
    return last_id


# ============================================================
#  SIDEBAR
# ============================================================
st.sidebar.markdown("### ⚙️ Mon Compte")
st.sidebar.caption(f"Connecté : {st.session_state.get('user_email', '')}")
st.sidebar.caption(f"🏢 Structure : {st.session_state.get('structure_nom', 'Non assignée')}")

peut_ia, nb_req, quota_max = peut_utiliser_ia(st.session_state.get("user_email", ""))
if quota_max >= 999999:
    st.sidebar.success("👑 Compte PRO illimité")
else:
    st.sidebar.info(f"Requêtes IA utilisées : {nb_req} / {quota_max}")

if IA_DISPONIBLE:
    st.sidebar.success("🔑 Clé API Gemini chargée (.secrets)")
else:
    st.sidebar.warning("⚠️ Clé API Gemini non configurée (fonctions IA indisponibles)")

if st.sidebar.button("🚪 Se déconnecter"):
    st.session_state["password_correct"] = False
    st.rerun()

st.sidebar.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)

if st.session_state.get("is_admin", False):
    st.sidebar.markdown("### 👑 Administration")

    with st.sidebar.expander("➕ Créer un accès structure/utilisateur"):
        df_structures_existantes = charger_df("SELECT nom FROM structures ORDER BY nom")
        with st.form("form_add_user"):
            p_structure_existante = st.selectbox(
                "Structure existante (ou laisser vide pour en créer une nouvelle)",
                [""] + df_structures_existantes["nom"].tolist() if not df_structures_existantes.empty else [""]
            )
            p_structure_nouvelle = st.text_input("OU nom d'une nouvelle structure")
            p_email = st.text_input("Email du nouvel utilisateur")
            p_pwd = st.text_input("Mot de passe temporaire")
            p_duree = st.number_input("Durée d'accès (jours)", min_value=1, value=30)
            btn_add = st.form_submit_button("Créer l'accès")

            if btn_add and p_email and p_pwd:
                nom_structure_finale = p_structure_nouvelle.strip() if p_structure_nouvelle.strip() else p_structure_existante
                if not nom_structure_finale:
                    st.error("Merci d'indiquer une structure (existante ou nouvelle).")
                else:
                    structure_id_new = get_or_create_structure(nom_structure_finale)
                    date_fin_calc = (datetime.date.today() + datetime.timedelta(days=int(p_duree))).isoformat()
                    try:
                        executer(
                            """INSERT INTO utilisateurs (email, password, date_fin_essai, est_admin, nb_requetes_ia, quota_max, structure_id)
                               VALUES (?, ?, ?, 0, 0, 20, ?)""",
                            (p_email, hacher_mdp(p_pwd), date_fin_calc, structure_id_new)
                        )
                        st.success(f"Accès créé pour {p_email} (structure : {nom_structure_finale}) jusqu'au {datetime.date.fromisoformat(date_fin_calc).strftime('%d/%m/%Y')} ! Mot de passe à communiquer : **{p_pwd}**")
                    except Exception as e_add:
                        st.error(f"Erreur : {e_add}")

    with st.sidebar.expander("🔑 Changer mon mot de passe"):
        with st.form("form_changer_mdp"):
            n1 = st.text_input("Nouveau mot de passe", type="password")
            n2 = st.text_input("Confirmer", type="password")
            btn_pwd = st.form_submit_button("Mettre à jour")
            if btn_pwd:
                if not n1 or n1 != n2:
                    st.error("Les mots de passe ne correspondent pas ou sont vides.")
                elif len(n1) < 8:
                    st.error("8 caractères minimum.")
                else:
                    executer("UPDATE utilisateurs SET password = ? WHERE email = ?", (hacher_mdp(n1), st.session_state["user_email"]))
                    st.success("Mot de passe mis à jour.")

    with st.sidebar.expander("📊 Quotas IA & Remise à 0"):
        df_users = charger_df("SELECT email, nb_requetes_ia, quota_max, statut_abonnement, date_fin_essai FROM utilisateurs")
        st.dataframe(df_users, use_container_width=True, hide_index=True)
        email_reset = st.text_input("Email à réinitialiser")
        if st.button("Remettre le quota à 0"):
            if email_reset:
                reinitialiser_quota_ia(email_reset)
                st.success("Quota réinitialisé.")

st.sidebar.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)

# ============================================================
#  MENU PRINCIPAL
# ============================================================
st.sidebar.markdown("### 📋 Menu principal")

_liste_onglets = [
    "🏠 Tableau de bord",
    "🧑‍🤝‍🧑 Vivier & Sourcing Direct",
    "🎯 Matching IA",
    "❤️ Portefeuille Bénéficiaires",
    "📝 Documents & Transmissions",
    "📅 Plannings, Tournées & Urgences",
    "✅ Conformité & Suivi",
    "👤 Mon Profil",
]
if st.session_state.get("is_admin", False):
    _liste_onglets.append("🛠️ Administration")

onglet = st.sidebar.radio("Navigation", _liste_onglets, label_visibility="collapsed")

st.markdown(f"# {onglet}")
st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)


# ============================================================
#  ONGLET 0 : TABLEAU DE BORD — ALERTES CENTRALISÉES
# ============================================================
if onglet == "🏠 Tableau de bord":

    sid = st.session_state["structure_id"]
    aujourdhui = datetime.date.today()
    seuil_60j = aujourdhui + datetime.timedelta(days=60)

    # --- Métriques globales ---
    col1, col2, col3, col4 = st.columns(4)
    nb_benef = len(charger_df("SELECT id FROM beneficiaires WHERE statut='Actif' AND structure_id=?", (sid,)))
    nb_interv = len(charger_df("SELECT id FROM intervenants WHERE statut_dispo='Disponible' AND structure_id=?", (sid,)))
    nb_plan_semaine = len(charger_df(
        "SELECT id FROM interventions WHERE structure_id=? AND date_intervention BETWEEN ? AND ? AND statut != 'Annulé'",
        (sid, aujourdhui.isoformat(), (aujourdhui + datetime.timedelta(days=7)).isoformat())
    ))
    nb_urgences = len(charger_df("SELECT id FROM interventions WHERE statut='Urgence à pourvoir' AND structure_id=?", (sid,)))

    col1.metric("👥 Bénéficiaires actifs", nb_benef)
    col2.metric("🧑‍⚕️ Intervenants dispo", nb_interv)
    col3.metric("📅 Interventions (7j)", nb_plan_semaine)
    col4.metric("🚨 Urgences en cours", nb_urgences, delta=f"-{nb_urgences}" if nb_urgences > 0 else None, delta_color="inverse")

    st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)

    # --- Alertes ---
    st.subheader("🔔 Alertes & points d'attention")

    alertes_rouges = []
    alertes_oranges = []
    alertes_bleues = []

    # 1. Habilitations expirées
    df_habs_exp = charger_df("""
        SELECT h.type_habilitation, h.date_expiration,
               v.prenom || ' ' || v.nom as intervenant
        FROM habilitations h
        LEFT JOIN intervenants v ON h.intervenant_id = v.id
        WHERE h.structure_id = ? AND h.date_expiration < ?
        ORDER BY h.date_expiration
    """, (sid, aujourdhui.isoformat()))
    for _, h in df_habs_exp.iterrows():
        alertes_rouges.append(f"🔴 Habilitation <b>{h['type_habilitation']}</b> de <b>{h['intervenant']}</b> expirée depuis le {h['date_expiration']}")

    # 2. Habilitations expirant dans moins de 60 jours
    df_habs_60 = charger_df("""
        SELECT h.type_habilitation, h.date_expiration,
               v.prenom || ' ' || v.nom as intervenant
        FROM habilitations h
        LEFT JOIN intervenants v ON h.intervenant_id = v.id
        WHERE h.structure_id = ? AND h.date_expiration >= ? AND h.date_expiration <= ?
        ORDER BY h.date_expiration
    """, (sid, aujourdhui.isoformat(), seuil_60j.isoformat()))
    for _, h in df_habs_60.iterrows():
        jours = (datetime.date.fromisoformat(h['date_expiration']) - aujourdhui).days
        alertes_oranges.append(f"🟠 Habilitation <b>{h['type_habilitation']}</b> de <b>{h['intervenant']}</b> expire dans <b>{jours} jour(s)</b> ({h['date_expiration']})")

    # 3. Interventions non confirmées (statut Urgence à pourvoir)
    df_urg = charger_df("""
        SELECT i.date_intervention, i.heure_debut, i.heure_fin, i.type_intervention,
               b.prenom || ' ' || b.nom as beneficiaire
        FROM interventions i
        LEFT JOIN beneficiaires b ON i.beneficiaire_id = b.id
        WHERE i.statut = 'Urgence à pourvoir' AND i.structure_id = ?
        ORDER BY i.date_intervention
    """, (sid,))
    for _, u in df_urg.iterrows():
        alertes_rouges.append(f"🚨 Intervention <b>non couverte</b> : {u['date_intervention']} {u['heure_debut']}–{u['heure_fin']} ({u['type_intervention']}) — Bénéficiaire : {u['beneficiaire']}")

    # 4. Bénéficiaires sans intervenant attitré
    df_sans_attitré = charger_df("""
        SELECT prenom || ' ' || nom as nom_complet
        FROM beneficiaires
        WHERE structure_id = ? AND statut = 'Actif'
          AND (intervenant_attitré_id IS NULL OR intervenant_attitré_id = 0)
        ORDER BY nom
    """, (sid,))
    for _, b in df_sans_attitré.iterrows():
        alertes_bleues.append(f"ℹ️ <b>{b['nom_complet']}</b> n'a pas d'intervenant attitré défini")

    total_alertes = len(alertes_rouges) + len(alertes_oranges) + len(alertes_bleues)

    if total_alertes == 0:
        st.markdown("""
            <div class="oc-card oc-card-ok">
                <b>✅ Tout est en ordre !</b> Aucune alerte active pour votre structure.
            </div>
        """, unsafe_allow_html=True)
    else:
        # Alertes rouges (critiques)
        if alertes_rouges:
            with st.expander(f"🔴 Alertes critiques ({len(alertes_rouges)})", expanded=True):
                for a in alertes_rouges:
                    st.markdown(f"""
                        <div class="alert-box alert-box-rouge">
                            <div class="alert-content"><span class="alert-detail">{a}</span></div>
                        </div>
                    """, unsafe_allow_html=True)

        # Alertes oranges (à surveiller)
        if alertes_oranges:
            with st.expander(f"🟠 À renouveler prochainement ({len(alertes_oranges)})", expanded=True):
                for a in alertes_oranges:
                    st.markdown(f"""
                        <div class="alert-box alert-box-orange">
                            <div class="alert-content"><span class="alert-detail">{a}</span></div>
                        </div>
                    """, unsafe_allow_html=True)

        # Alertes bleues (informations)
        if alertes_bleues:
            with st.expander(f"ℹ️ Points d'attention ({len(alertes_bleues)})"):
                for a in alertes_bleues:
                    st.markdown(f"""
                        <div class="alert-box alert-box-bleu">
                            <div class="alert-content"><span class="alert-detail">{a}</span></div>
                        </div>
                    """, unsafe_allow_html=True)

    st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)

    # --- Interventions du jour ---
    st.subheader(f"📅 Interventions du jour — {date_fr(aujourdhui, 'long')}")
    df_jour = charger_df("""
        SELECT i.heure_debut, i.heure_fin, i.type_intervention, i.statut,
               b.prenom || ' ' || b.nom as beneficiaire,
               v.prenom || ' ' || v.nom as intervenant
        FROM interventions i
        LEFT JOIN beneficiaires b ON i.beneficiaire_id = b.id
        LEFT JOIN intervenants v ON i.intervenant_id = v.id
        WHERE i.date_intervention = ? AND i.structure_id = ?
        ORDER BY i.heure_debut
    """, (aujourdhui.isoformat(), sid))

    if df_jour.empty:
        st.caption("Aucune intervention planifiée aujourd'hui.")
    else:
        for _, row in df_jour.iterrows():
            couleur_p = {"Planifié": "#4c8dfa", "Urgence à pourvoir": "#e0554f", "Réalisé": "#3fae74", "Annulé": "#8996a3"}.get(row["statut"], "#8996a3")
            st.markdown(f"""
                <div class="oc-card" style="border-left-color:{couleur_p}; padding:12px 16px;">
                    <b>{row['heure_debut']} – {row['heure_fin']}</b> · {row['type_intervention']}
                    <span class="oc-badge" style="background:{couleur_p}; float:right;">{row['statut']}</span><br>
                    <span style="color:#b8c2cc;">👤 {row['beneficiaire']} &nbsp;•&nbsp; 🧑‍⚕️ {row['intervenant']}</span>
                </div>
            """, unsafe_allow_html=True)


# ============================================================
#  ONGLET 1 : VIVIER & SOURCING DIRECT
# ============================================================
if onglet == "🧑‍🤝‍🧑 Vivier & Sourcing Direct":

    tab_liste, tab_ajout, tab_sourcing = st.tabs(["📋 Vivier actuel", "➕ Ajouter un intervenant", "🔎 Sourcing externe direct"])

    with tab_liste:
        df_interv = charger_df("SELECT * FROM intervenants WHERE structure_id = ? ORDER BY date_ajout DESC", (st.session_state["structure_id"],))
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            filtre_type = st.selectbox("Filtrer par statut", ["Tous", "Interne", "Vivier candidat", "Externe ponctuel"])
        with col_f2:
            filtre_dispo = st.selectbox("Filtrer par disponibilité", ["Toutes", "Disponible", "En mission", "Indisponible"])

        df_affiche = df_interv.copy()
        if filtre_type != "Tous":
            df_affiche = df_affiche[df_affiche["type_statut"] == filtre_type]
        if filtre_dispo != "Toutes":
            df_affiche = df_affiche[df_affiche["statut_dispo"] == filtre_dispo]

        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Total intervenants", len(df_interv))
        col_m2.metric("Disponibles maintenant", len(df_interv[df_interv["statut_dispo"] == "Disponible"]) if not df_interv.empty else 0)
        col_m3.metric("Vivier candidats (hors poste)", len(df_interv[df_interv["type_statut"] == "Vivier candidat"]) if not df_interv.empty else 0)

        st.markdown("<br>", unsafe_allow_html=True)

        if df_affiche.empty:
            st.info("Aucun intervenant enregistré pour ce filtre.")
        else:
            for _, row in df_affiche.iterrows():
                couleur = {"Disponible": "#3fae74", "En mission": "#d99a3d", "Indisponible": "#e0554f"}.get(row["statut_dispo"], "#8996a3")
                st.markdown(f"""
                    <div class="oc-card" style="border-left-color:{couleur};">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="font-size:17px; font-weight:700;">{row['prenom']} {row['nom']}</span>
                            <span class="oc-badge" style="background-color:{couleur};">{row['statut_dispo']}</span>
                        </div>
                        <div style="color:#b8c2cc; font-size:13px; margin-top:4px;">{row['type_statut']} • {row['zone_geo'] or 'Zone non précisée'} • Source : {row['source'] or 'N/C'}</div>
                        <div style="color:#e6ecf2; margin-top:8px;"><b>Compétences :</b> {row['competences'] or 'Non renseigné'}</div>
                    </div>
                """, unsafe_allow_html=True)

                with st.expander(f"Détails / actions — {row['prenom']} {row['nom']}"):
                    col_a, col_b = st.columns(2)
                    with col_a:
                        nouveau_statut = st.selectbox(
                            "Changer la disponibilité", ["Disponible", "En mission", "Indisponible"],
                            index=["Disponible", "En mission", "Indisponible"].index(row["statut_dispo"]) if row["statut_dispo"] in ["Disponible", "En mission", "Indisponible"] else 0,
                            key=f"dispo_{row['id']}"
                        )
                        if st.button("Mettre à jour", key=f"maj_dispo_{row['id']}"):
                            executer("UPDATE intervenants SET statut_dispo = ? WHERE id = ? AND structure_id = ?", (nouveau_statut, row["id"], st.session_state["structure_id"]))
                            st.success("Statut mis à jour.")
                            st.rerun()
                    with col_b:
                        if st.button("🗑️ Supprimer cet intervenant", key=f"del_{row['id']}"):
                            executer("DELETE FROM intervenants WHERE id = ? AND structure_id = ?", (row["id"], st.session_state["structure_id"]))
                            st.warning("Intervenant supprimé.")
                            st.rerun()
                    st.write(f"**Parcours / expérience :** {row['experience_texte'] or 'Non renseigné'}")
                    st.write(f"**Disponibilités déclarées :** {row['disponibilites'] or 'Non renseigné'}")
                    st.write(f"**Contact :** {row['telephone'] or ''} — {row['email'] or ''}")

    with tab_ajout:
        st.subheader("Ajouter un intervenant au vivier (interne ou candidat)")
        with st.form("form_ajout_intervenant"):
            col1, col2 = st.columns(2)
            with col1:
                nom = st.text_input("Nom")
                prenom = st.text_input("Prénom")
                telephone = st.text_input("Téléphone")
                email_i = st.text_input("Email")
                type_statut = st.selectbox("Statut", ["Interne", "Vivier candidat", "Externe ponctuel"])
            with col2:
                competences = st.text_area("Compétences / gestes techniques maîtrisés", placeholder="Ex : toilette, aide au lever, transfert, stimulation cognitive...")
                experience_texte = st.text_area("Parcours professionnel (texte libre)", placeholder="Décrire le parcours, y compris expériences hors secteur médico-social — utile pour le matching IA.")
                zone_geo = st.text_input("Zone géographique / secteur d'intervention")
                disponibilites = st.text_input("Disponibilités (ex : lun-ven matin, weekends...)")
                source = st.selectbox("Source de recrutement", ["Vivier interne", "CVthèque", "Annonce", "Réseau / cooptation", "Candidature spontanée"])

            submit_add = st.form_submit_button("Ajouter au vivier")
            if submit_add and nom and prenom:
                executer(
                    """INSERT INTO intervenants (structure_id, nom, prenom, telephone, email, type_statut, competences, experience_texte, zone_geo, disponibilites, statut_dispo, source, date_ajout)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Disponible', ?, ?)""",
                    (st.session_state["structure_id"], nom, prenom, telephone, email_i, type_statut, competences, experience_texte, zone_geo, disponibilites, source, datetime.date.today().isoformat())
                )
                st.success(f"{prenom} {nom} ajouté(e) au vivier.")
                st.rerun()

    with tab_sourcing:
        st.subheader("🔎 Sourcing externe direct — réduire la dépendance aux agences d'intérim")
        st.caption("Génère des liens de recherche ciblés vers les principales sources de candidats, à ouvrir manuellement.")

        col_s1, col_s2 = st.columns(2)
        with col_s1:
            metier_recherche = st.text_input("Métier recherché", value="Auxiliaire de vie")
        with col_s2:
            zone_recherche = st.text_input("Zone géographique", value="")

        if st.button("Générer les liens de sourcing"):
            requete = f"{metier_recherche} {zone_recherche}".strip()
            requete_url = urllib.parse.quote(requete)
            st.markdown(f"""
                <div class="oc-card">
                    <b>🔗 LinkedIn</b><br>
                    <a href="https://www.linkedin.com/search/results/people/?keywords={requete_url}" target="_blank">Rechercher des profils LinkedIn</a>
                </div>
                <div class="oc-card">
                    <b>🔗 CVthèques ouvertes (recherche web ciblée)</b><br>
                    <a href="https://www.google.com/search?q={requete_url}+CV+filetype:pdf" target="_blank">Rechercher des CV publics (PDF)</a>
                </div>
                <div class="oc-card">
                    <b>🔗 Groupes Facebook emploi local</b><br>
                    <a href="https://www.facebook.com/search/groups/?q={requete_url}+emploi" target="_blank">Rechercher des groupes emploi</a>
                </div>
                <div class="oc-card">
                    <b>🔗 Indeed / France Travail</b><br>
                    <a href="https://www.indeed.fr/jobs?q={requete_url}" target="_blank">Voir les profils similaires sur Indeed</a>
                </div>
            """, unsafe_allow_html=True)


# ============================================================
#  ONGLET 2 : MATCHING IA
# ============================================================
if onglet == "🎯 Matching IA":
    st.caption("Croise les besoins spécifiques d'un bénéficiaire avec les compétences, habilitations et la proximité des intervenants du vivier.")

    df_benef = charger_df("SELECT * FROM beneficiaires WHERE statut = 'Actif' AND structure_id = ? ORDER BY nom", (st.session_state["structure_id"],))
    df_interv_dispo = charger_df("SELECT * FROM intervenants WHERE statut_dispo != 'Indisponible' AND structure_id = ?", (st.session_state["structure_id"],))

    if df_benef.empty:
        st.info("Ajoute d'abord un bénéficiaire dans l'onglet « Portefeuille Bénéficiaires » pour lancer un matching.")
    elif df_interv_dispo.empty:
        st.info("Aucun intervenant disponible dans le vivier pour l'instant.")
    else:
        benef_labels = {f"{r['prenom']} {r['nom']}": r['id'] for _, r in df_benef.iterrows()}
        benef_choisi_label = st.selectbox("Bénéficiaire concerné", list(benef_labels.keys()))
        benef_id = benef_labels[benef_choisi_label]
        benef_row = df_benef[df_benef["id"] == benef_id].iloc[0]

        st.markdown(f"""
            <div class="oc-card">
                <b>Besoins récurrents :</b> {benef_row.get('besoins_recurrents', '') or 'Non renseigné'}<br>
                <b>Gestes techniques requis :</b> {benef_row.get('gestes_techniques', '') or 'Non renseigné'}<br>
                <b>Horaires souhaités :</b> {benef_row.get('besoins_horaires', '') or 'Non renseigné'}<br>
                <b>Niveau de dépendance :</b> {benef_row.get('niveau_dependance', '') or 'Non renseigné'}
            </div>
        """, unsafe_allow_html=True)

        if st.button("🎯 Lancer le matching IA sur le vivier disponible"):
            if not IA_DISPONIBLE:
                st.error("Clé API Gemini non configurée — impossible de lancer le matching IA.")
            else:
                autorise, _, _ = peut_utiliser_ia(st.session_state["user_email"])
                if not autorise:
                    st.error("Quota de requêtes IA atteint pour votre compte.")
                else:
                    resultats = []
                    barre = st.progress(0)
                    total = len(df_interv_dispo)

                    for idx, (_, interv) in enumerate(df_interv_dispo.iterrows()):
                        df_habs = charger_df("SELECT * FROM habilitations WHERE intervenant_id = ? AND structure_id = ?", (int(interv["id"]), st.session_state["structure_id"]))
                        habs_txt = "; ".join([f"{h['type_habilitation']} (exp. {h['date_expiration']})" for _, h in df_habs.iterrows()]) or "Aucune habilitation enregistrée"

                        prompt = f"""
                        Tu es un coordinateur expert en aide à domicile (SAAD/SSIAD). Évalue l'adéquation entre
                        le besoin du bénéficiaire et le profil de cet intervenant.

                        CONSIGNES :
                        1. Compare les gestes techniques requis avec les compétences de l'intervenant.
                        2. Vérifie si les habilitations listées couvrent les besoins.
                        3. Repère aussi les compétences transférables issues du parcours.
                        4. Tiens compte de la compatibilité des disponibilités et de la zone géographique.

                        Renvoie STRICTEMENT un objet JSON avec les clés :
                        - 'score_competences': entier 0-100
                        - 'score_habilitations': entier 0-100
                        - 'score_global': entier 0-100
                        - 'competences_transferables': liste de chaînes "compétence — issue de [expérience précise]"
                        - 'alerte_habilitation': texte court si une habilitation obligatoire semble manquante, sinon chaîne vide
                        - 'justification': synthèse de 2-3 lignes

                        BESOIN DU BÉNÉFICIAIRE :
                        Besoins récurrents : {benef_row.get('besoins_recurrents', '')}
                        Gestes techniques requis : {benef_row.get('gestes_techniques', '')}
                        Horaires souhaités : {benef_row.get('besoins_horaires', '')}
                        Niveau de dépendance : {benef_row.get('niveau_dependance', '')}

                        PROFIL INTERVENANT :
                        Compétences déclarées : {interv['competences']}
                        Parcours professionnel : {interv['experience_texte']}
                        Habilitations : {habs_txt}
                        Zone géographique : {interv['zone_geo']}
                        Disponibilités : {interv['disponibilites']}
                        """
                        try:
                            reponse = model.generate_content(prompt)
                            txt = reponse.text.strip().replace("```json", "").replace("```", "").strip()
                            data = json.loads(txt)
                            data["intervenant_nom"] = f"{interv['prenom']} {interv['nom']}"
                            data["intervenant_statut"] = interv["type_statut"]
                            resultats.append(data)
                        except Exception:
                            pass

                        barre.progress((idx + 1) / total)

                    incrementer_quota_ia(st.session_state["user_email"])
                    st.session_state["resultats_matching_medico"] = sorted(resultats, key=lambda x: int(x.get("score_global", 0)), reverse=True)

        if "resultats_matching_medico" in st.session_state and st.session_state["resultats_matching_medico"]:
            st.markdown("### 📊 Résultats du matching")
            for res in st.session_state["resultats_matching_medico"]:
                score = int(res.get("score_global", 0))
                couleur = "#3fae74" if score >= 70 else ("#d99a3d" if score >= 40 else "#e0554f")
                st.markdown(f"""
                    <div class="oc-card" style="border-left-color:{couleur};">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="font-size:17px; font-weight:700;">{res.get('intervenant_nom')}</span>
                            <span class="oc-badge" style="background-color:{couleur};">{score}%</span>
                        </div>
                        <div style="color:#b8c2cc; font-size:13px; margin-top:4px;">{res.get('intervenant_statut', '')}</div>
                    </div>
                """, unsafe_allow_html=True)

                if res.get("alerte_habilitation"):
                    st.warning(f"⚠️ {res['alerte_habilitation']}")

                with st.expander("Détails du matching"):
                    col_s1, col_s2 = st.columns(2)
                    col_s1.caption("Compétences / gestes techniques")
                    col_s1.progress(min(1.0, int(res.get("score_competences", 0)) / 100))
                    col_s2.caption("Habilitations")
                    col_s2.progress(min(1.0, int(res.get("score_habilitations", 0)) / 100))

                    transf = res.get("competences_transferables", [])
                    if transf:
                        st.markdown("**🌱 Compétences transférables détectées**")
                        for t in transf:
                            st.markdown(f"- {t}")

                    st.markdown("**Synthèse**")
                    st.write(res.get("justification", ""))


# ============================================================
#  ONGLET 3 : PORTEFEUILLE BÉNÉFICIAIRES (avec fiche simplifiée)
# ============================================================
if onglet == "❤️ Portefeuille Bénéficiaires":

    tab_liste_b, tab_ajout_b = st.tabs(["📋 Bénéficiaires suivis", "➕ Ajouter un bénéficiaire"])

    df_interv_all = charger_df("SELECT id, prenom, nom FROM intervenants WHERE structure_id = ? ORDER BY nom", (st.session_state["structure_id"],))
    interv_map = {r['id']: f"{r['prenom']} {r['nom']}" for _, r in df_interv_all.iterrows()}

    with tab_liste_b:
        df_b = charger_df("SELECT * FROM beneficiaires WHERE structure_id = ? ORDER BY nom", (st.session_state["structure_id"],))
        st.metric("Bénéficiaires suivis", len(df_b[df_b["statut"] == "Actif"]) if not df_b.empty else 0)

        if df_b.empty:
            st.info("Aucun bénéficiaire enregistré pour l'instant.")
        else:
            for _, row in df_b.iterrows():
                couleur = "#3fae74" if row["statut"] == "Actif" else "#8996a3"
                attitré_nom = interv_map.get(row.get("intervenant_attitré_id"), "Non défini")

                st.markdown(f"""
                    <div class="oc-card" style="border-left-color:{couleur};">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="font-size:17px; font-weight:700;">{row['prenom']} {row['nom']}</span>
                            <span class="oc-badge" style="background-color:{couleur};">{row['statut']}</span>
                        </div>
                        <div style="color:#b8c2cc; font-size:13px; margin-top:4px;">
                            📍 {row['adresse'] or '—'} &nbsp;•&nbsp; 🧑‍⚕️ Attitré : {attitré_nom}
                        </div>
                    </div>
                """, unsafe_allow_html=True)

                with st.expander(f"📋 Fiche — {row['prenom']} {row['nom']}"):
                    # --- FICHE BÉNÉFICIAIRE SIMPLIFIÉE ---
                    col_fiche1, col_fiche2 = st.columns(2)

                    with col_fiche1:
                        st.markdown(f"""
                            <div class="fiche-section">
                                <h4>📍 Coordonnées</h4>
                                <div class="fiche-row"><span class="fiche-label">Adresse</span><span class="fiche-value">{row['adresse'] or '—'}</span></div>
                                <div class="fiche-row"><span class="fiche-label">Téléphone</span><span class="fiche-value">{row['telephone'] or '—'}</span></div>
                                <div class="fiche-row"><span class="fiche-label">Dépendance</span><span class="fiche-value">{row['niveau_dependance'] or '—'}</span></div>
                            </div>
                        """, unsafe_allow_html=True)

                        st.markdown(f"""
                            <div class="fiche-section">
                                <h4>🚨 Contact d'urgence</h4>
                                <div class="fiche-row"><span class="fiche-label">Nom / lien</span><span class="fiche-value">{row.get('contact_urgence_nom', '') or row.get('referent_famille', '') or '—'}</span></div>
                                <div class="fiche-row"><span class="fiche-label">Téléphone</span><span class="fiche-value">{row.get('contact_urgence_tel', '') or '—'}</span></div>
                            </div>
                        """, unsafe_allow_html=True)

                    with col_fiche2:
                        st.markdown(f"""
                            <div class="fiche-section">
                                <h4>🔄 Besoins récurrents</h4>
                                <div class="fiche-value">{row.get('besoins_recurrents', '') or row.get('besoins_horaires', '') or '—'}</div>
                            </div>
                        """, unsafe_allow_html=True)

                        st.markdown(f"""
                            <div class="fiche-section">
                                <h4>🧑‍⚕️ Intervenant attitré</h4>
                                <div class="fiche-value" style="font-size:15px; font-weight:600;">{attitré_nom}</div>
                            </div>
                        """, unsafe_allow_html=True)

                        if row.get('notes'):
                            st.markdown(f"""
                                <div class="fiche-section">
                                    <h4>📝 Notes</h4>
                                    <div class="fiche-value">{row['notes']}</div>
                                </div>
                            """, unsafe_allow_html=True)

                    st.markdown("<br>", unsafe_allow_html=True)

                    # --- Actions ---
                    col_x, col_y, col_z = st.columns(3)
                    with col_x:
                        nouveau_statut_b = st.selectbox("Statut", ["Actif", "Inactif"], index=0 if row["statut"] == "Actif" else 1, key=f"statut_b_{row['id']}")
                        if st.button("Mettre à jour le statut", key=f"maj_b_{row['id']}"):
                            executer("UPDATE beneficiaires SET statut = ? WHERE id = ? AND structure_id = ?", (nouveau_statut_b, row["id"], st.session_state["structure_id"]))
                            st.success("Statut mis à jour.")
                            st.rerun()
                    with col_y:
                        # Changement d'intervenant attitré
                        options_interv = {"Non défini": None}
                        options_interv.update({v: k for k, v in interv_map.items()})
                        idx_att = 0
                        att_id = row.get("intervenant_attitré_id")
                        if att_id and att_id in interv_map:
                            labels_list = list(options_interv.keys())
                            att_label = interv_map[att_id]
                            if att_label in labels_list:
                                idx_att = labels_list.index(att_label)
                        nouvel_attitré = st.selectbox("Intervenant attitré", list(options_interv.keys()), index=idx_att, key=f"att_{row['id']}")
                        if st.button("Définir comme attitré", key=f"set_att_{row['id']}"):
                            executer("UPDATE beneficiaires SET intervenant_attitré_id = ? WHERE id = ? AND structure_id = ?",
                                     (options_interv[nouvel_attitré], row["id"], st.session_state["structure_id"]))
                            st.success("Intervenant attitré mis à jour.")
                            st.rerun()
                    with col_z:
                        if st.button("🗑️ Supprimer", key=f"del_b_{row['id']}"):
                            executer("DELETE FROM beneficiaires WHERE id = ? AND structure_id = ?", (row["id"], st.session_state["structure_id"]))
                            st.warning("Bénéficiaire supprimé.")
                            st.rerun()

    with tab_ajout_b:
        st.subheader("Ajouter un bénéficiaire")
        with st.form("form_ajout_beneficiaire"):
            col1, col2 = st.columns(2)
            with col1:
                nom_b = st.text_input("Nom *")
                prenom_b = st.text_input("Prénom *")
                adresse_b = st.text_input("Adresse")
                telephone_b = st.text_input("Téléphone")
                niveau_dep = st.selectbox("Niveau de dépendance (GIR)", ["GIR 1", "GIR 2", "GIR 3", "GIR 4", "GIR 5", "GIR 6", "Non évalué"])
            with col2:
                contact_urgence_nom_b = st.text_input("Contact d'urgence (nom + lien de parenté)", placeholder="Ex : Marie Dupont, fille")
                contact_urgence_tel_b = st.text_input("Téléphone contact d'urgence")
                besoins_recurrents_b = st.text_area("Besoins récurrents", placeholder="Ex : aide à la toilette matin, repas midi, ménage lundi et jeudi")
                gestes_b = st.text_area("Gestes techniques requis", placeholder="Ex : aide à la toilette, transfert avec lève-personne...")
                horaires_b = st.text_input("Besoins horaires", placeholder="Ex : matin 8h-9h30, soir 19h-20h")
                notes_b = st.text_area("Notes complémentaires")

            # Sélection de l'intervenant attitré dès la création
            interv_options_b = {"Non défini": None}
            if not df_interv_all.empty:
                interv_options_b.update({f"{r['prenom']} {r['nom']}": r['id'] for _, r in df_interv_all.iterrows()})
            interv_att_b = st.selectbox("Intervenant attitré (optionnel)", list(interv_options_b.keys()))

            submit_b = st.form_submit_button("Ajouter le bénéficiaire")
            if submit_b and nom_b and prenom_b:
                executer(
                    """INSERT INTO beneficiaires (structure_id, nom, prenom, adresse, telephone, niveau_dependance,
                       gestes_techniques, besoins_horaires, referent_famille, notes, statut, date_creation,
                       contact_urgence_nom, contact_urgence_tel, besoins_recurrents, intervenant_attitré_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Actif', ?, ?, ?, ?, ?)""",
                    (st.session_state["structure_id"], nom_b, prenom_b, adresse_b, telephone_b, niveau_dep,
                     gestes_b, horaires_b, contact_urgence_nom_b, notes_b, datetime.date.today().isoformat(),
                     contact_urgence_nom_b, contact_urgence_tel_b, besoins_recurrents_b,
                     interv_options_b[interv_att_b])
                )
                st.success(f"{prenom_b} {nom_b} ajouté(e) au portefeuille.")
                st.rerun()


# ============================================================
#  ONGLET 4 : DOCUMENTS & TRANSMISSIONS
# ============================================================
if onglet == "📝 Documents & Transmissions":
    st.caption("Assistant de rédaction de comptes-rendus, fiches de liaison et documents professionnels — à relire avant diffusion.")

    df_benef2 = charger_df("SELECT * FROM beneficiaires WHERE statut = 'Actif' AND structure_id = ? ORDER BY nom", (st.session_state["structure_id"],))
    df_interv2 = charger_df("SELECT * FROM intervenants WHERE structure_id = ? ORDER BY nom", (st.session_state["structure_id"],))

    if df_benef2.empty:
        st.info("Ajoute d'abord un bénéficiaire pour rédiger une transmission.")
    else:
        col1, col2 = st.columns(2)
        with col1:
            benef_labels2 = {f"{r['prenom']} {r['nom']}": r['id'] for _, r in df_benef2.iterrows()}
            benef_choisi2 = st.selectbox("Bénéficiaire", list(benef_labels2.keys()), key="benef_doc")
        with col2:
            interv_labels2 = {"Non spécifié": None}
            interv_labels2.update({f"{r['prenom']} {r['nom']}": r['id'] for _, r in df_interv2.iterrows()})
            interv_choisi2 = st.selectbox("Intervenant rédacteur", list(interv_labels2.keys()), key="interv_doc")

        type_doc = st.selectbox("Type de document", ["Fiche de liaison", "Compte-rendu de visite", "Transmission d'équipe", "Note d'incident"])
        notes_brutes = st.text_area("Notes brutes / observations à retranscrire", height=150, placeholder="Ex : Mme X en forme ce matin, a bien mangé, légère douleur au genou signalée, RDV kiné à confirmer...")

        if st.button("✍️ Générer le document avec l'IA"):
            if not IA_DISPONIBLE:
                st.error("Clé API Gemini non configurée.")
            elif not notes_brutes:
                st.warning("Ajoute quelques notes brutes avant de générer le document.")
            else:
                autorise2, _, _ = peut_utiliser_ia(st.session_state["user_email"])
                if not autorise2:
                    st.error("Quota de requêtes IA atteint.")
                else:
                    prompt_doc = f"""
                    Tu es un(e) coordinateur/trice en structure d'aide à domicile. Rédige un document professionnel
                    de type "{type_doc}" à partir des notes brutes ci-dessous, dans un style clair, factuel et
                    professionnel adapté à une transmission d'équipe ou à un dossier bénéficiaire.
                    Ne rajoute aucune information médicale ou fait qui ne figure pas dans les notes fournies.
                    Reste synthétique (10-15 lignes maximum).

                    Bénéficiaire : {benef_choisi2}
                    Notes brutes : {notes_brutes}
                    """
                    try:
                        reponse_doc = model.generate_content(prompt_doc)
                        texte_genere = reponse_doc.text.strip()
                        incrementer_quota_ia(st.session_state["user_email"])
                        st.session_state["doc_genere"] = texte_genere
                    except Exception as e_doc:
                        st.error(f"Erreur de génération : {e_doc}")

        if st.session_state.get("doc_genere"):
            st.markdown("### 📄 Document généré (à relire avant diffusion)")
            texte_final = st.text_area("Contenu (modifiable)", value=st.session_state["doc_genere"], height=250)

            col_save, col_pdf = st.columns(2)
            with col_save:
                if st.button("💾 Enregistrer dans le dossier bénéficiaire"):
                    executer(
                        """INSERT INTO documents_transmissions (structure_id, beneficiaire_id, intervenant_id, date_creation, type_document, contenu)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (st.session_state["structure_id"], benef_labels2[benef_choisi2], interv_labels2[interv_choisi2], datetime.date.today().isoformat(), type_doc, texte_final)
                    )
                    st.success("Document enregistré dans le dossier du bénéficiaire.")
            with col_pdf:
                try:
                    pdf_bytes = creer_pdf_transmission(benef_choisi2, interv_choisi2, datetime.date.today().strftime("%d/%m/%Y"), texte_final)
                    st.download_button("⬇️ Télécharger en PDF", data=bytes(pdf_bytes), file_name=f"{type_doc}_{benef_choisi2}.pdf", mime="application/pdf")
                except Exception as e_pdf:
                    st.error(f"Erreur PDF : {e_pdf}")

        st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
        st.markdown("### 🗂️ Historique des documents")
        df_docs = charger_df("""
            SELECT d.date_creation, d.type_document, b.prenom || ' ' || b.nom as beneficiaire, d.contenu
            FROM documents_transmissions d
            LEFT JOIN beneficiaires b ON d.beneficiaire_id = b.id
            WHERE d.structure_id = ?
            ORDER BY d.date_creation DESC
        """, (st.session_state["structure_id"],))
        if df_docs.empty:
            st.caption("Aucun document enregistré pour l'instant.")
        else:
            st.dataframe(df_docs, use_container_width=True, hide_index=True)


# ============================================================
#  ONGLET 5 : PLANNINGS, TOURNÉES & URGENCES
# ============================================================
if onglet == "📅 Plannings, Tournées & Urgences":

    tab_planning_visuel, tab_planning_ajout, tab_remplacement, tab_urgence = st.tabs([
        "📊 Planning hebdomadaire",
        "➕ Planifier une intervention",
        "🔄 Remplacements & absences",
        "🚨 Urgences en cours"
    ])

    sid = st.session_state["structure_id"]
    df_benef3 = charger_df("SELECT * FROM beneficiaires WHERE statut = 'Actif' AND structure_id = ? ORDER BY nom", (sid,))
    df_interv3 = charger_df("SELECT * FROM intervenants WHERE structure_id = ? ORDER BY nom", (sid,))

    # ----------------------------------------------------------
    #  TAB 1 : PLANNING HEBDOMADAIRE VISUEL
    # ----------------------------------------------------------
    with tab_planning_visuel:
        st.subheader("📊 Planning hebdomadaire — vue par intervenant")

        # Navigation semaine
        if "semaine_offset" not in st.session_state:
            st.session_state["semaine_offset"] = 0

        col_nav1, col_nav2, col_nav3 = st.columns([1, 3, 1])
        with col_nav1:
            if st.button("◀ Semaine précédente"):
                st.session_state["semaine_offset"] -= 1
                st.rerun()
        with col_nav3:
            if st.button("Semaine suivante ▶"):
                st.session_state["semaine_offset"] += 1
                st.rerun()

        offset = st.session_state["semaine_offset"]
        aujourdhui = datetime.date.today()
        lundi_semaine = aujourdhui - datetime.timedelta(days=aujourdhui.weekday()) + datetime.timedelta(weeks=offset)
        dimanche_semaine = lundi_semaine + datetime.timedelta(days=6)

        with col_nav2:
            st.markdown(f"<div style='text-align:center; color:#4c8dfa; font-weight:700; font-size:16px;'>Semaine du {date_fr(lundi_semaine, 'medium')} au {date_fr(dimanche_semaine, 'medium')}</div>", unsafe_allow_html=True)

        if st.button("🔙 Revenir à la semaine courante", key="reset_semaine"):
            st.session_state["semaine_offset"] = 0
            st.rerun()

        # Chargement des interventions de la semaine
        df_semaine = charger_df("""
            SELECT i.id, i.date_intervention, i.heure_debut, i.heure_fin, i.type_intervention, i.statut,
                   b.prenom || ' ' || b.nom as beneficiaire,
                   v.id as intervenant_id, v.prenom || ' ' || v.nom as intervenant
            FROM interventions i
            LEFT JOIN beneficiaires b ON i.beneficiaire_id = b.id
            LEFT JOIN intervenants v ON i.intervenant_id = v.id
            WHERE i.date_intervention BETWEEN ? AND ? AND i.structure_id = ?
            ORDER BY i.heure_debut
        """, (lundi_semaine.isoformat(), dimanche_semaine.isoformat(), sid))

        dates_semaine = [lundi_semaine + datetime.timedelta(days=i) for i in range(7)]

        if df_interv3.empty:
            st.info("Aucun intervenant enregistré. Ajoutez des intervenants pour visualiser le planning.")
        else:
            # En-tête du tableau (jours en français via date_fr)
            headers_html = '<th class="col-intervenant">Intervenant</th>'
            for d in dates_semaine:
                is_today = (d == aujourdhui)
                style_today = " style='background:rgba(47,124,246,0.25); color:#4c8dfa;'" if is_today else ""
                label = date_fr(d, "semaine")   # ex. "Ven. 21/08"
                headers_html += f'<th{style_today}>{label}</th>'

            rows_html = ""
            for _, interv in df_interv3.iterrows():
                row_html = f'<td class="col-intervenant">{interv["prenom"]} {interv["nom"]}</td>'

                for d in dates_semaine:
                    date_str = d.isoformat()
                    interventions_du_jour = df_semaine[
                        (df_semaine["date_intervention"] == date_str) &
                        (df_semaine["intervenant_id"] == interv["id"])
                    ] if not df_semaine.empty else pd.DataFrame()

                    if interventions_du_jour.empty:
                        row_html += '<td><div class="planning-empty">·</div></td>'
                    else:
                        cell_content = ""
                        for _, interv_row in interventions_du_jour.iterrows():
                            css_extra = ""
                            if interv_row["statut"] == "Urgence à pourvoir":
                                css_extra = " urgence"
                            elif interv_row["statut"] == "Réalisé":
                                css_extra = " realise"
                            elif interv_row["statut"] == "Annulé":
                                css_extra = " annule"
                            cell_content += f"""
                                <div class="planning-cell{css_extra}">
                                    <b>{interv_row['heure_debut']}–{interv_row['heure_fin']}</b><br>
                                    {interv_row['beneficiaire']}<br>
                                    <span style='color:#8996a3;font-size:11px;'>{interv_row['type_intervention']}</span>
                                </div>
                            """
                        row_html += f'<td>{cell_content}</td>'

                rows_html += f"<tr>{row_html}</tr>"

            # Légende
            st.markdown("""
                <div style="display:flex; gap:16px; margin-bottom:12px; flex-wrap:wrap;">
                    <span><span style="display:inline-block;width:12px;height:12px;background:#2f7cf6;border-radius:2px;margin-right:4px;"></span>Planifié</span>
                    <span><span style="display:inline-block;width:12px;height:12px;background:#e0554f;border-radius:2px;margin-right:4px;"></span>Urgence à pourvoir</span>
                    <span><span style="display:inline-block;width:12px;height:12px;background:#3fae74;border-radius:2px;margin-right:4px;"></span>Réalisé</span>
                    <span><span style="display:inline-block;width:12px;height:12px;background:#8996a3;border-radius:2px;margin-right:4px;"></span>Annulé</span>
                </div>
            """, unsafe_allow_html=True)

            planning_html = f"""
                <div style="overflow-x:auto;">
                <table class="planning-table">
                    <thead><tr>{headers_html}</tr></thead>
                    <tbody>{rows_html}</tbody>
                </table>
                </div>
            """
            st.markdown(planning_html, unsafe_allow_html=True)

            # Stats de la semaine
            st.markdown("<br>", unsafe_allow_html=True)
            if not df_semaine.empty:
                col_s1, col_s2, col_s3, col_s4 = st.columns(4)
                col_s1.metric("Total interventions", len(df_semaine))
                col_s2.metric("Réalisées", len(df_semaine[df_semaine["statut"] == "Réalisé"]))
                col_s3.metric("Urgences", len(df_semaine[df_semaine["statut"] == "Urgence à pourvoir"]))
                col_s4.metric("Planifiées", len(df_semaine[df_semaine["statut"] == "Planifié"]))

    # ----------------------------------------------------------
    #  TAB 2 : PLANIFIER UNE INTERVENTION
    # ----------------------------------------------------------
    with tab_planning_ajout:
        st.subheader("➕ Planifier une nouvelle intervention")
        if df_benef3.empty or df_interv3.empty:
            st.info("Ajoute au moins un bénéficiaire et un intervenant pour créer un planning.")
        else:
            with st.form("form_planning"):
                col1, col2, col3 = st.columns(3)
                with col1:
                    benef_labels3 = {f"{r['prenom']} {r['nom']}": r['id'] for _, r in df_benef3.iterrows()}
                    benef_p = st.selectbox("Bénéficiaire", list(benef_labels3.keys()))
                with col2:
                    interv_labels3 = {f"{r['prenom']} {r['nom']}": r['id'] for _, r in df_interv3.iterrows()}
                    interv_p = st.selectbox("Intervenant", list(interv_labels3.keys()))
                with col3:
                    type_interv_p = st.selectbox("Type d'intervention", ["Aide à la toilette", "Aide au repas", "Ménage", "Accompagnement", "Soins", "Autre"])

                col4, col5, col6 = st.columns(3)
                with col4:
                    date_p = st.date_input("Date", value=datetime.date.today())
                with col5:
                    heure_debut_p = st.time_input("Heure de début")
                with col6:
                    heure_fin_p = st.time_input("Heure de fin")

                notes_p = st.text_input("Notes (optionnel)")
                submit_p = st.form_submit_button("Planifier l'intervention")

                if submit_p:
                    executer(
                        """INSERT INTO interventions (structure_id, beneficiaire_id, intervenant_id, date_intervention, heure_debut, heure_fin, type_intervention, statut, notes)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 'Planifié', ?)""",
                        (sid, benef_labels3[benef_p], interv_labels3[interv_p], date_p.isoformat(), heure_debut_p.strftime("%H:%M"), heure_fin_p.strftime("%H:%M"), type_interv_p, notes_p)
                    )
                    st.success("Intervention planifiée.")
                    st.rerun()

        st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
        st.markdown("### 📋 Interventions à venir")
        df_plan = charger_df("""
            SELECT i.id, i.date_intervention, i.heure_debut, i.heure_fin, i.type_intervention, i.statut,
                   b.prenom || ' ' || b.nom as beneficiaire, v.prenom || ' ' || v.nom as intervenant
            FROM interventions i
            LEFT JOIN beneficiaires b ON i.beneficiaire_id = b.id
            LEFT JOIN intervenants v ON i.intervenant_id = v.id
            WHERE i.date_intervention >= ? AND i.structure_id = ?
            ORDER BY i.date_intervention, i.heure_debut
        """, (datetime.date.today().isoformat(), sid))

        if df_plan.empty:
            st.caption("Aucune intervention planifiée à venir.")
        else:
            for _, row in df_plan.iterrows():
                couleur_p = {"Planifié": "#4c8dfa", "Urgence à pourvoir": "#e0554f", "Réalisé": "#3fae74", "Annulé": "#8996a3"}.get(row["statut"], "#8996a3")
                col_info, col_action = st.columns([4, 1])
                with col_info:
                    st.markdown(f"""
                        <div class="oc-card" style="border-left-color:{couleur_p}; padding:12px 16px;">
                            <b>{row['date_intervention']} — {row['heure_debut']} à {row['heure_fin']}</b> · {row['type_intervention']}<br>
                            <span style="color:#b8c2cc;">👤 {row['beneficiaire']} • 🧑‍⚕️ {row['intervenant']} • <span class="oc-badge" style="background-color:{couleur_p}; padding:2px 10px;">{row['statut']}</span></span>
                        </div>
                    """, unsafe_allow_html=True)
                with col_action:
                    if row["statut"] == "Planifié":
                        if st.button("✅ Réalisée", key=f"realise_{row['id']}"):
                            executer("UPDATE interventions SET statut = 'Réalisé' WHERE id = ? AND structure_id = ?", (row["id"], sid))
                            st.rerun()
                    if row["statut"] not in ["Urgence à pourvoir", "Annulé", "Réalisé"]:
                        if st.button("🚨 Absence", key=f"absence_{row['id']}"):
                            executer("UPDATE interventions SET statut = 'Urgence à pourvoir' WHERE id = ? AND structure_id = ?", (row["id"], sid))
                            st.rerun()

    # ----------------------------------------------------------
    #  TAB 3 : REMPLACEMENTS & ABSENCES (nouveau)
    # ----------------------------------------------------------
    with tab_remplacement:
        st.subheader("🔄 Gestion des remplacements & absences")
        st.caption("Signalez une absence : l'outil identifie automatiquement les remplaçants disponibles ayant les habilitations requises pour le bénéficiaire concerné.")

        df_plan_rem = charger_df("""
            SELECT i.id, i.date_intervention, i.heure_debut, i.heure_fin, i.type_intervention, i.statut,
                   b.id as benef_id, b.prenom || ' ' || b.nom as beneficiaire,
                   b.gestes_techniques, b.besoins_recurrents,
                   v.id as intervenant_id, v.prenom || ' ' || v.nom as intervenant
            FROM interventions i
            LEFT JOIN beneficiaires b ON i.beneficiaire_id = b.id
            LEFT JOIN intervenants v ON i.intervenant_id = v.id
            WHERE i.date_intervention >= ? AND i.structure_id = ? AND i.statut = 'Planifié'
            ORDER BY i.date_intervention, i.heure_debut
        """, (datetime.date.today().isoformat(), sid))

        if df_plan_rem.empty:
            st.info("Aucune intervention planifiée à venir. Planifiez d'abord des interventions.")
        else:
            # Sélection de l'intervention concernée
            options_interventions = {
                f"{r['date_intervention']} {r['heure_debut']}–{r['heure_fin']} | {r['beneficiaire']} ← {r['intervenant']}": r['id']
                for _, r in df_plan_rem.iterrows()
            }
            interv_choisie_label = st.selectbox("Intervention concernée par l'absence", list(options_interventions.keys()))
            interv_choisie_id = options_interventions[interv_choisie_label]
            interv_choisie_row = df_plan_rem[df_plan_rem["id"] == interv_choisie_id].iloc[0]

            st.markdown(f"""
                <div class="oc-card oc-card-warning">
                    <b>📋 Intervention sélectionnée</b><br>
                    📅 {interv_choisie_row['date_intervention']} — {interv_choisie_row['heure_debut']} à {interv_choisie_row['heure_fin']}<br>
                    👤 Bénéficiaire : <b>{interv_choisie_row['beneficiaire']}</b><br>
                    🧑‍⚕️ Intervenant prévu : <b>{interv_choisie_row['intervenant']}</b><br>
                    🩺 Type : {interv_choisie_row['type_intervention']}
                </div>
            """, unsafe_allow_html=True)

            col_ab1, col_ab2 = st.columns(2)
            with col_ab1:
                if st.button("🚨 Déclarer l'absence & chercher un remplaçant", type="primary"):
                    # Marquer l'intervention en urgence
                    executer("UPDATE interventions SET statut = 'Urgence à pourvoir', intervenant_id = NULL WHERE id = ? AND structure_id = ?",
                             (interv_choisie_id, sid))
                    # Marquer l'intervenant indisponible
                    if interv_choisie_row["intervenant_id"]:
                        executer("UPDATE intervenants SET statut_dispo = 'Indisponible' WHERE id = ? AND structure_id = ?",
                                 (interv_choisie_row["intervenant_id"], sid))

                    # Recherche des remplaçants : disponibles, habilitations compatibles
                    besoins_benef = interv_choisie_row.get("gestes_techniques", "") or interv_choisie_row.get("besoins_recurrents", "") or ""

                    # Intervenants disponibles (hors l'absent)
                    df_dispo_rem = charger_df("""
                        SELECT v.id, v.prenom, v.nom, v.competences, v.zone_geo, v.disponibilites, v.email
                        FROM intervenants v
                        WHERE v.structure_id = ? AND v.statut_dispo = 'Disponible'
                        AND v.id != ?
                        ORDER BY v.nom
                    """, (sid, interv_choisie_row["intervenant_id"] or 0))

                    # Récupération des habilitations requises pour le bénéficiaire
                    # (on identifie les habilitations des interventions passées pour ce bénéficiaire)
                    date_interv = interv_choisie_row["date_intervention"]

                    st.session_state["remplaçants_trouves"] = []
                    st.session_state["intervention_remplacement_id"] = interv_choisie_id
                    st.session_state["besoins_remplacement"] = besoins_benef

                    if df_dispo_rem.empty:
                        st.session_state["remplaçants_trouves"] = []
                    else:
                        candidats_scores = []
                        for _, cand in df_dispo_rem.iterrows():
                            # Vérification simple des habilitations du candidat
                            df_habs_cand = charger_df("""
                                SELECT type_habilitation, date_expiration FROM habilitations
                                WHERE intervenant_id = ? AND structure_id = ?
                                AND date_expiration >= ?
                            """, (cand["id"], sid, datetime.date.today().isoformat()))

                            habs_valides = [h["type_habilitation"] for _, h in df_habs_cand.iterrows()]

                            # Score simple de compatibilité
                            score = 50  # Base
                            besoins_lower = besoins_benef.lower()
                            comp_lower = (cand["competences"] or "").lower()

                            # Bonus compétences
                            mots_cles = ["toilette", "repas", "ménage", "transfert", "accompagnement", "soins", "aide"]
                            for mot in mots_cles:
                                if mot in besoins_lower and mot in comp_lower:
                                    score += 10

                            candidats_scores.append({
                                "id": cand["id"],
                                "nom": f"{cand['prenom']} {cand['nom']}",
                                "zone": cand["zone_geo"] or "—",
                                "competences": cand["competences"] or "—",
                                "disponibilites": cand["disponibilites"] or "—",
                                "email": cand["email"] or "",
                                "habilitations": ", ".join(habs_valides) if habs_valides else "Aucune enregistrée",
                                "score": min(score, 100)
                            })

                        st.session_state["remplaçants_trouves"] = sorted(candidats_scores, key=lambda x: x["score"], reverse=True)

                    st.success("Absence déclarée. Remplaçants disponibles identifiés ci-dessous.")
                    st.rerun()

            with col_ab2:
                st.caption("L'intervenant sera marqué indisponible et l'intervention passera en urgence à pourvoir.")

            # Affichage des remplaçants trouvés
            if st.session_state.get("remplaçants_trouves") is not None and st.session_state.get("intervention_remplacement_id") == interv_choisie_id:
                remplaçants = st.session_state["remplaçants_trouves"]

                st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
                st.markdown("### 👥 Remplaçants disponibles & compatibles")

                if not remplaçants:
                    st.warning("⚠️ Aucun intervenant disponible actuellement dans le vivier. Consultez l'onglet Urgences pour la gestion manuelle.")
                else:
                    for i, cand in enumerate(remplaçants):
                        couleur_score = "#3fae74" if cand["score"] >= 70 else ("#d99a3d" if cand["score"] >= 40 else "#4c8dfa")
                        col_c1, col_c2 = st.columns([4, 1])
                        with col_c1:
                            st.markdown(f"""
                                <div class="oc-card" style="border-left-color:{couleur_score};">
                                    <div style="display:flex;justify-content:space-between;align-items:center;">
                                        <span style="font-weight:700;font-size:15px;">#{i+1} — {cand['nom']}</span>
                                        <span class="oc-badge" style="background:{couleur_score};">Score {cand['score']}%</span>
                                    </div>
                                    <div style="color:#b8c2cc;font-size:13px;margin-top:6px;">
                                        📍 Zone : {cand['zone']} &nbsp;|&nbsp; ⏰ Dispo : {cand['disponibilites']}<br>
                                        🎓 Habilitations valides : {cand['habilitations']}<br>
                                        🛠️ Compétences : {cand['competences'][:80]}{'...' if len(cand['competences']) > 80 else ''}
                                    </div>
                                </div>
                            """, unsafe_allow_html=True)
                        with col_c2:
                            if st.button(f"✅ Assigner", key=f"assign_rem_{cand['id']}_{interv_choisie_id}"):
                                executer(
                                    "UPDATE interventions SET intervenant_id = ?, statut = 'Planifié' WHERE id = ? AND structure_id = ?",
                                    (cand["id"], interv_choisie_id, sid)
                                )
                                executer(
                                    "UPDATE intervenants SET statut_dispo = 'En mission' WHERE id = ? AND structure_id = ?",
                                    (cand["id"], sid)
                                )
                                st.session_state.pop("remplaçants_trouves", None)
                                st.success(f"✅ {cand['nom']} assigné(e) en remplacement.")
                                st.rerun()

                            # Envoi email si boîte configurée
                            cfg_mail = st.session_state.get("mail_config", {})
                            if cand["email"] and cfg_mail.get("email"):
                                if st.button("📧 Email", key=f"mail_rem_{cand['id']}_{interv_choisie_id}"):
                                    row_urg = df_plan_rem[df_plan_rem["id"] == interv_choisie_id].iloc[0]
                                    ok_m, msg_m = envoyer_email_intervenant(
                                        cand["email"],
                                        f"Remplacement urgent le {row_urg['date_intervention']}",
                                        f"Bonjour {cand['nom']},\n\nUne intervention est à pourvoir le {row_urg['date_intervention']} de {row_urg['heure_debut']} à {row_urg['heure_fin']} ({row_urg['type_intervention']}).\nPouvez-vous assurer ce remplacement ?\n\nMerci.",
                                        cfg_mail["email"], cfg_mail["password"]
                                    )
                                    if ok_m:
                                        st.success("Email envoyé.")
                                    else:
                                        st.error(msg_m)

    # ----------------------------------------------------------
    #  TAB 4 : URGENCES EN COURS (repris de l'existant)
    # ----------------------------------------------------------
    with tab_urgence:
        st.subheader("🚨 Interventions à pourvoir en urgence")
        st.caption("🤖 L'agent IA classe les intervenants disponibles par pertinence pour chaque remplacement, sollicite automatiquement le meilleur candidat, puis relance le suivant en cascade en cas de refus.")
        df_urgences = charger_df("""
            SELECT i.id, i.date_intervention, i.heure_debut, i.heure_fin, i.type_intervention,
                   b.id as beneficiaire_id, b.prenom || ' ' || b.nom as beneficiaire,
                   b.gestes_techniques
            FROM interventions i
            LEFT JOIN beneficiaires b ON i.beneficiaire_id = b.id
            WHERE i.statut = 'Urgence à pourvoir' AND i.structure_id = ?
            ORDER BY i.date_intervention, i.heure_debut
        """, (sid,))

        if df_urgences.empty:
            st.success("✅ Aucune urgence en cours.")
        else:
            for _, urg in df_urgences.iterrows():
                st.markdown(f"""
                    <div class="oc-card oc-card-alert">
                        <b>🚨 {urg['date_intervention']} — {urg['heure_debut']} à {urg['heure_fin']}</b><br>
                        Bénéficiaire : {urg['beneficiaire']} • {urg['type_intervention']}
                    </div>
                """, unsafe_allow_html=True)

                sollicitation_active = charger_df("""
                    SELECT s.id, s.intervenant_id, s.score_global, s.justification, s.alerte_habilitation, s.date_envoi,
                           v.prenom || ' ' || v.nom as intervenant
                    FROM sollicitations_urgence s
                    LEFT JOIN intervenants v ON s.intervenant_id = v.id
                    WHERE s.intervention_id = ? AND s.structure_id = ? AND s.statut = 'En attente'
                    ORDER BY s.date_envoi DESC LIMIT 1
                """, (int(urg["id"]), sid))

                if not sollicitation_active.empty:
                    sol = sollicitation_active.iloc[0]
                    st.markdown(f"""
                        <div class="oc-card oc-card-warning">
                            🤖 <b>Candidat sollicité automatiquement :</b> {sol['intervenant']} (score IA : {sol['score_global']}%)<br>
                            <span style="color:#b8c2cc;">Envoyé le {str(sol['date_envoi'])[:16].replace('T', ' ')} — {sol['justification']}</span><br>
                            ⏳ En attente de réponse du candidat.
                        </div>
                    """, unsafe_allow_html=True)
                    if sol["alerte_habilitation"]:
                        st.warning(f"⚠️ {sol['alerte_habilitation']}")
                    col_a, col_r = st.columns(2)
                    with col_a:
                        if st.button("✅ A accepté", key=f"accepte_{sol['id']}"):
                            traiter_reponse_sollicitation(int(sol["id"]), int(urg["id"]), int(sol["intervenant_id"]), "Accepté", sid)
                            st.success("Remplacement confirmé, planning mis à jour automatiquement.")
                            st.rerun()
                    with col_r:
                        if st.button("❌ A refusé → relancer le suivant", key=f"refuse_{sol['id']}"):
                            traiter_reponse_sollicitation(int(sol["id"]), int(urg["id"]), int(sol["intervenant_id"]), "Refusé", sid)
                            st.rerun()
                else:
                    df_dispo = charger_df("SELECT * FROM intervenants WHERE statut_dispo = 'Disponible' AND structure_id = ?", (sid,))
                    if df_dispo.empty:
                        st.warning("Aucun intervenant disponible actuellement dans le vivier.")
                    elif IA_DISPONIBLE:
                        if st.button("🤖 Lancer la recherche IA & solliciter automatiquement", key=f"cascade_{urg['id']}"):
                            autorise, _, _ = peut_utiliser_ia(st.session_state["user_email"])
                            if not autorise:
                                st.error("Quota de requêtes IA atteint pour votre compte.")
                            else:
                                classement = classer_candidats_urgence(urg, df_dispo, sid)
                                incrementer_quota_ia(st.session_state["user_email"])
                                candidat = prochain_candidat_non_sollicite(int(urg["id"]), classement, sid)
                                if not candidat:
                                    st.warning("Tous les intervenants disponibles ont déjà été sollicités sans succès pour cette urgence.")
                                else:
                                    ok, msg = solliciter_candidat_urgence(urg, candidat, sid)
                                    if ok:
                                        st.success(f"Meilleur candidat identifié : {candidat['intervenant_nom']} ({candidat.get('score_global', 0)}%) — sollicitation envoyée automatiquement.")
                                        st.rerun()
                                    else:
                                        st.error(msg)
                    else:
                        st.info("Clé API Gemini non configurée — sollicitation manuelle uniquement.")
                        for _, cand in df_dispo.iterrows():
                            col_c1, col_c2 = st.columns([3, 1])
                            with col_c1:
                                st.write(f"👤 **{cand['prenom']} {cand['nom']}** — {cand['zone_geo'] or 'zone non précisée'} — {cand['competences'] or ''}")
                            with col_c2:
                                if cand["email"] and st.session_state.get("mail_config", {}).get("email"):
                                    if st.button("📧 Solliciter", key=f"solliciter_manuel_{urg['id']}_{cand['id']}"):
                                        cfg = st.session_state["mail_config"]
                                        sujet = f"Remplacement urgent le {urg['date_intervention']}"
                                        corps = (
                                            f"Bonjour {cand['prenom']},\n\n"
                                            f"Une intervention est à pourvoir en urgence le {urg['date_intervention']} "
                                            f"de {urg['heure_debut']} à {urg['heure_fin']} ({urg['type_intervention']}).\n"
                                            f"Merci de nous confirmer votre disponibilité au plus vite.\n\nMerci."
                                        )
                                        ok, msg = envoyer_email_intervenant(cand["email"], sujet, corps, cfg["email"], cfg["password"])
                                        if ok:
                                            st.success("Sollicitation envoyée.")
                                        else:
                                            st.error(msg)

                df_hist = charger_df("""
                    SELECT s.date_envoi, s.statut, s.score_global, v.prenom || ' ' || v.nom as intervenant
                    FROM sollicitations_urgence s
                    LEFT JOIN intervenants v ON s.intervenant_id = v.id
                    WHERE s.intervention_id = ? AND s.structure_id = ?
                    ORDER BY s.date_envoi DESC
                """, (int(urg["id"]), sid))
                if not df_hist.empty:
                    with st.expander("📜 Historique des sollicitations pour cette urgence"):
                        st.dataframe(df_hist, use_container_width=True, hide_index=True)

                if st.button("✅ Marquer comme pourvue manuellement", key=f"resolu_{urg['id']}"):
                    executer("UPDATE interventions SET statut = 'Planifié' WHERE id = ? AND structure_id = ?", (urg["id"], sid))
                    st.rerun()

                st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)


# ============================================================
#  ONGLET 6 : CONFORMITÉ & SUIVI
# ============================================================
if onglet == "✅ Conformité & Suivi":

    tab_suivi, tab_ajout_hab = st.tabs(["📋 Suivi des habilitations", "➕ Ajouter une habilitation"])

    df_interv4 = charger_df("SELECT * FROM intervenants WHERE structure_id = ? ORDER BY nom", (st.session_state["structure_id"],))

    with tab_suivi:
        aujourdhui = datetime.date.today()
        seuil_alerte = aujourdhui + datetime.timedelta(days=60)

        df_habs_all = charger_df("""
            SELECT h.id, h.type_habilitation, h.date_obtention, h.date_expiration,
                   v.prenom || ' ' || v.nom as intervenant, v.id as intervenant_id
            FROM habilitations h
            LEFT JOIN intervenants v ON h.intervenant_id = v.id
            WHERE h.structure_id = ?
            ORDER BY h.date_expiration
        """, (st.session_state["structure_id"],))

        if df_habs_all.empty:
            st.info("Aucune habilitation enregistrée pour l'instant.")
        else:
            df_habs_all["date_expiration_dt"] = pd.to_datetime(df_habs_all["date_expiration"], errors="coerce").dt.date

            en_retard = df_habs_all[df_habs_all["date_expiration_dt"] < aujourdhui]
            bientot = df_habs_all[(df_habs_all["date_expiration_dt"] >= aujourdhui) & (df_habs_all["date_expiration_dt"] <= seuil_alerte)]
            ok = df_habs_all[df_habs_all["date_expiration_dt"] > seuil_alerte]

            col1, col2, col3 = st.columns(3)
            col1.metric("🔴 Expirées", len(en_retard))
            col2.metric("🟠 À renouveler (< 60j)", len(bientot))
            col3.metric("🟢 À jour", len(ok))

            if not en_retard.empty:
                st.markdown("#### 🔴 Habilitations expirées")
                for _, h in en_retard.iterrows():
                    st.markdown(f"""<div class="oc-card oc-card-alert">
                        <b>{h['intervenant']}</b> — {h['type_habilitation']} — expirée depuis le {h['date_expiration']}
                    </div>""", unsafe_allow_html=True)

            if not bientot.empty:
                st.markdown("#### 🟠 À renouveler prochainement")
                for _, h in bientot.iterrows():
                    st.markdown(f"""<div class="oc-card oc-card-warning">
                        <b>{h['intervenant']}</b> — {h['type_habilitation']} — expire le {h['date_expiration']}
                    </div>""", unsafe_allow_html=True)

            if not ok.empty:
                with st.expander("🟢 Voir les habilitations à jour"):
                    st.dataframe(ok[["intervenant", "type_habilitation", "date_obtention", "date_expiration"]], use_container_width=True, hide_index=True)

    with tab_ajout_hab:
        st.subheader("Ajouter une habilitation / certification")
        if df_interv4.empty:
            st.info("Ajoute d'abord un intervenant.")
        else:
            with st.form("form_ajout_hab"):
                interv_labels4 = {f"{r['prenom']} {r['nom']}": r['id'] for _, r in df_interv4.iterrows()}
                interv_hab = st.selectbox("Intervenant", list(interv_labels4.keys()))
                type_hab = st.selectbox("Type d'habilitation", [
                    "Diplôme AES", "DEAES", "PSC1 / SST", "Permis B", "Visite médecine du travail",
                    "Habilitation gestes et postures", "AFGSU", "Autre"
                ])
                date_obt = st.date_input("Date d'obtention")
                date_exp = st.date_input("Date d'expiration")
                submit_hab = st.form_submit_button("Ajouter")

                if submit_hab:
                    executer(
                        "INSERT INTO habilitations (structure_id, intervenant_id, type_habilitation, date_obtention, date_expiration) VALUES (?, ?, ?, ?, ?)",
                        (st.session_state["structure_id"], interv_labels4[interv_hab], type_hab, date_obt.isoformat(), date_exp.isoformat())
                    )
                    st.success("Habilitation ajoutée.")
                    st.rerun()


# ============================================================
#  ONGLET 7 : MON PROFIL
# ============================================================
if onglet == "👤 Mon Profil":

    st.caption(f"Structure : **{st.session_state.get('structure_nom', 'Non assignée')}**")

    st.subheader("📧 Ma boîte mail (sollicitations & réception)")
    with st.form("form_mail_config"):
        cfg_actuelle = st.session_state.get("mail_config", {})
        mail_e = st.text_input("Adresse e-mail d'envoi", value=cfg_actuelle.get("email", ""))
        mail_p = st.text_input("Mot de passe d'application", type="password")
        mail_i = st.text_input("Serveur IMAP", value=cfg_actuelle.get("imap", "imap.gmail.com"))
        submit_mail = st.form_submit_button("Enregistrer ma boîte mail")

        if submit_mail:
            executer(
                "UPDATE utilisateurs SET mail_perso = ?, mail_password = ?, mail_imap = ? WHERE email = ?",
                (mail_e, mail_p if mail_p else cfg_actuelle.get("password", ""), mail_i, st.session_state["user_email"])
            )
            st.session_state["mail_config"] = {"email": mail_e, "password": mail_p or cfg_actuelle.get("password", ""), "imap": mail_i}
            st.success("Boîte mail enregistrée.")


# ============================================================
#  ONGLET ADMIN-ONLY : ADMINISTRATION
# ============================================================
if onglet == "🛠️ Administration" and st.session_state.get("is_admin", False):

    st.subheader("🏢 Vue par structure (toutes structures confondues)")
    df_structures_vue = charger_df("""
        SELECT s.nom as structure,
               (SELECT COUNT(*) FROM beneficiaires WHERE structure_id = s.id) as beneficiaires,
               (SELECT COUNT(*) FROM intervenants WHERE structure_id = s.id) as intervenants,
               (SELECT COUNT(*) FROM interventions WHERE structure_id = s.id) as interventions,
               (SELECT COUNT(*) FROM documents_transmissions WHERE structure_id = s.id) as documents
        FROM structures s
        ORDER BY s.nom
    """)
    st.dataframe(df_structures_vue, use_container_width=True, hide_index=True)

    st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)

    st.subheader("👥 Utilisateurs & accès")
    df_users_admin = charger_df("""
        SELECT u.email, s.nom as structure, u.statut_abonnement, u.date_fin_essai, u.nb_requetes_ia, u.quota_max
        FROM utilisateurs u
        LEFT JOIN structures s ON u.structure_id = s.id
        ORDER BY s.nom
    """)
    st.dataframe(df_users_admin, use_container_width=True, hide_index=True)

    st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
    st.subheader("🗑️ Gestion et suppression d'un accès")

    df_users_del = charger_df("SELECT id, email FROM utilisateurs WHERE email != 'admin@omnicoord.fr'")

    if not df_users_del.empty:
        user_to_delete = st.selectbox(
            "Sélectionner l'utilisateur à supprimer",
            options=df_users_del["email"].tolist(),
            key="select_user_to_delete"
        )

        confirm_del = st.checkbox("Je confirme vouloir supprimer cet accès et toutes les données associées à cette structure")

        if st.button("🗑️ Supprimer définitivement l'utilisateur"):
            if confirm_del:
                try:
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    cursor.execute("SELECT structure_id FROM utilisateurs WHERE email = ?", (user_to_delete,))
                    res = cursor.fetchone()
                    if res:
                        struct_id = res[0]
                        cursor.execute("DELETE FROM utilisateurs WHERE email = ?", (user_to_delete,))
                        cursor.execute("SELECT COUNT(*) FROM utilisateurs WHERE structure_id = ?", (struct_id,))
                        remaining_users = cursor.fetchone()[0]
                        if remaining_users == 0:
                            cursor.execute("DELETE FROM beneficiaires WHERE structure_id = ?", (struct_id,))
                            cursor.execute("DELETE FROM intervenants WHERE structure_id = ?", (struct_id,))
                            cursor.execute("DELETE FROM interventions WHERE structure_id = ?", (struct_id,))
                            cursor.execute("DELETE FROM documents_transmissions WHERE structure_id = ?", (struct_id,))
                            cursor.execute("DELETE FROM structures WHERE id = ?", (struct_id,))
                        conn.commit()
                        conn.close()
                        st.success(f"L'accès pour {user_to_delete} a été supprimé avec succès !")
                        time.sleep(1.5)
                        st.rerun()
                    else:
                        st.error("Utilisateur introuvable.")
                except Exception as e:
                    st.error(f"Erreur lors de la suppression : {e}")
            else:
                st.warning("Veuillez cocher la case de confirmation pour procéder à la suppression.")
    else:
        st.info("Aucun autre utilisateur à supprimer pour le moment.")

    st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)

    st.subheader("🗄️ État global de la base de données")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Bénéficiaires (total)", len(charger_df("SELECT id FROM beneficiaires")))
    col2.metric("Intervenants (total)", len(charger_df("SELECT id FROM intervenants")))
    col3.metric("Interventions (total)", len(charger_df("SELECT id FROM interventions")))
    col4.metric("Documents (total)", len(charger_df("SELECT id FROM documents_transmissions")))

    st.caption(f"Base de données locale : `{DB_NAME}` (SQLite, mode WAL). Pensez à ne jamais versionner ce fichier sur GitHub (voir .gitignore).")

    st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
    st.subheader("🔐 Sécurité")
    st.write("- Mots de passe utilisateurs hachés avec **bcrypt**.")
    st.write("- Clé API Gemini chargée uniquement via les secrets Streamlit (jamais en dur dans le code).")
    st.write(f"- Statut clé Gemini : {'✅ Configurée' if IA_DISPONIBLE else '❌ Non configurée'}")
    st.write("- Cloisonnement des données actif : chaque structure ne voit que ses propres bénéficiaires, intervenants, plannings et documents.")
