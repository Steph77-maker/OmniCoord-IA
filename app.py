
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
        color: #e6ecf2;
    }

    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0c1f33 0%, #0a1929 100%);
        border-right: 1px solid rgba(137, 150, 163, 0.25);
    }

    h1, h2, h3 {
        color: #f2f5f8 !important;
        letter-spacing: 0.3px;
    }

    /* Correction globale pour rendre tous les textes et labels bien visibles */
    p, span, label, .stMarkdown, div[data-baseweb="select"] span {
        color: #e6ecf2 !important;
    }

    /* Visibilité spécifique pour les labels de filtres et selectbox */
    .stSelectbox label, .stTextInput label, .stNumberInput label, .stDateInput label {
        color: #b8c2cc !important;
        font-weight: 500;
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
    }

    .oc-card-alert {
        border-left: 4px solid var(--oc-alert) !important;
    }

    .oc-card-warning {
        border-left: 4px solid var(--oc-warning) !important;
    }

    .oc-card-ok {
        border-left: 4px solid var(--oc-success) !important;
    }

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
    }

    div[data-testid="stMetricValue"] {
        color: var(--oc-medical-blue-soft) !important;
    }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# --- SÉCURITÉ : HACHAGE DES MOTS DE PASSE (bcrypt) ---
def hacher_mdp(mot_de_passe_clair):
    """Retourne le hash bcrypt (str) d'un mot de passe en clair."""
    return bcrypt.hashpw(mot_de_passe_clair.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verifier_mdp(mot_de_passe_saisi, valeur_stockee):
    """Vérifie un mot de passe saisi contre la valeur stockée (hash bcrypt ou, pour
    compatibilité de migration, ancien texte en clair)."""
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


# --- ENVOI D'EMAIL (alertes urgences / demandes de remplacement) ---
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


# --- CALCUL DE PROXIMITÉ SIMPLIFIÉ (distance à vol d'oiseau si coordonnées fournies) ---
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
                statut_abonnement TEXT DEFAULT 'ESSAI'
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
                """INSERT INTO utilisateurs (email, password, date_fin_essai, est_admin, mail_perso, mail_password, mail_imap, nb_requetes_ia, quota_max, statut_abonnement)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("admin@omnicoord.fr", mdp_admin_hash, "2099-12-31", 1, default_mail, default_pwd, default_imap, 0, 999999, "PRO"),
            )
            conn_auth.commit()
        conn_auth.close()
    except Exception as e:
        st.error(f"Erreur d'initialisation du système d'authentification : {e}")


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
                        "SELECT password, date_fin_essai, est_admin, mail_perso, mail_password, mail_imap FROM utilisateurs WHERE email = ?",
                        (email_saisi,)
                    )
                    row = c.fetchone()
                    conn.close()

                    if row:
                        db_password, db_date_fin, db_is_admin, m_mail, m_pass, m_imap = row

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

# --- CONFIGURATION IA (clé indépendante de tout autre projet) ---
try:
    gemini_key = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel("gemini-2.0-flash")
    IA_DISPONIBLE = True
except Exception:
    IA_DISPONIBLE = False
    model = None


# ============================================================
#  TABLES MÉTIER : BÉNÉFICIAIRES, INTERVENANTS, HABILITATIONS, PLANNINGS
# ============================================================
def initialiser_tables_metier():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS beneficiaires (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT, prenom TEXT, adresse TEXT, telephone TEXT,
            niveau_dependance TEXT,
            pathologies TEXT,
            gestes_techniques TEXT,
            besoins_horaires TEXT,
            referent_famille TEXT,
            notes TEXT,
            statut TEXT DEFAULT 'Actif',
            date_creation TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS intervenants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT, prenom TEXT, telephone TEXT, email TEXT,
            type_statut TEXT,       -- Interne / Vivier candidat / Externe ponctuel
            competences TEXT,
            experience_texte TEXT,  -- texte libre du parcours, pour matching IA compétences transférables
            zone_geo TEXT,
            disponibilites TEXT,
            statut_dispo TEXT DEFAULT 'Disponible',  -- Disponible / En mission / Indisponible
            source TEXT,             -- Vivier interne / CVthèque / Annonce / Réseau / Cooptation
            date_ajout TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS habilitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            intervenant_id INTEGER,
            type_habilitation TEXT,   -- Diplôme AES, DEAES, PSC1, Permis B, Visite médicale, etc.
            date_obtention TEXT,
            date_expiration TEXT,
            FOREIGN KEY(intervenant_id) REFERENCES intervenants(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS interventions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            beneficiaire_id INTEGER,
            intervenant_id INTEGER,
            date_intervention TEXT,
            heure_debut TEXT,
            heure_fin TEXT,
            type_intervention TEXT,
            statut TEXT DEFAULT 'Planifié',  -- Planifié / Réalisé / Urgence à pourvoir / Annulé
            notes TEXT,
            FOREIGN KEY(beneficiaire_id) REFERENCES beneficiaires(id),
            FOREIGN KEY(intervenant_id) REFERENCES intervenants(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS documents_transmissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            beneficiaire_id INTEGER,
            intervenant_id INTEGER,
            date_creation TEXT,
            type_document TEXT,
            contenu TEXT,
            FOREIGN KEY(beneficiaire_id) REFERENCES beneficiaires(id),
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
#  SIDEBAR — COMPTE, IA, ADMIN
# ============================================================
st.sidebar.markdown("### ⚙️ Mon Compte")
st.sidebar.caption(f"Connecté : {st.session_state.get('user_email', '')}")

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
        with st.form("form_add_user"):
            p_email = st.text_input("Email du nouvel utilisateur")
            p_pwd = st.text_input("Mot de passe temporaire")
            p_duree = st.number_input("Durée d'accès (jours)", min_value=1, value=30)
            btn_add = st.form_submit_button("Créer l'accès")

            if btn_add and p_email and p_pwd:
                date_fin_calc = (datetime.date.today() + datetime.timedelta(days=int(p_duree))).isoformat()
                try:
                    executer(
                        """INSERT INTO utilisateurs (email, password, date_fin_essai, est_admin, nb_requetes_ia, quota_max)
                           VALUES (?, ?, ?, 0, 0, 20)""",
                        (p_email, hacher_mdp(p_pwd), date_fin_calc)
                    )
                    st.success(f"Accès créé pour {p_email} jusqu'au {datetime.date.fromisoformat(date_fin_calc).strftime('%d/%m/%Y')} ! Mot de passe à communiquer : **{p_pwd}**")
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
onglet = st.sidebar.radio(
    "Navigation",
    [
        "🧑‍🤝‍🧑 Vivier & Sourcing Direct",
        "🎯 Matching IA",
        "❤️ Portefeuille Bénéficiaires",
        "📝 Documents & Transmissions",
        "📅 Plannings, Tournées & Urgences",
        "✅ Conformité & Suivi",
        "🛠️ Administration & Paramètres",
    ],
    label_visibility="collapsed"
)

st.markdown(f"# {onglet}")
st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)


# ============================================================
#  ONGLET 1 : VIVIER & SOURCING DIRECT
# ============================================================
if onglet == "🧑‍🤝‍🧑 Vivier & Sourcing Direct":

    tab_liste, tab_ajout, tab_sourcing = st.tabs(["📋 Vivier actuel", "➕ Ajouter un intervenant", "🔎 Sourcing externe direct"])

    with tab_liste:
        df_interv = charger_df("SELECT * FROM intervenants ORDER BY date_ajout DESC")
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
                            executer("UPDATE intervenants SET statut_dispo = ? WHERE id = ?", (nouveau_statut, row["id"]))
                            st.success("Statut mis à jour.")
                            st.rerun()
                    with col_b:
                        if st.button("🗑️ Supprimer cet intervenant", key=f"del_{row['id']}"):
                            executer("DELETE FROM intervenants WHERE id = ?", (row["id"],))
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
                experience_texte = st.text_area("Parcours professionnel (texte libre)", placeholder="Décrire le parcours, y compris expériences hors secteur médico-social — utile pour le matching IA sur les compétences transférables.")
                zone_geo = st.text_input("Zone géographique / secteur d'intervention")
                disponibilites = st.text_input("Disponibilités (ex : lun-ven matin, weekends...)")
                source = st.selectbox("Source de recrutement", ["Vivier interne", "CVthèque", "Annonce", "Réseau / cooptation", "Candidature spontanée"])

            submit_add = st.form_submit_button("Ajouter au vivier")
            if submit_add and nom and prenom:
                executer(
                    """INSERT INTO intervenants (nom, prenom, telephone, email, type_statut, competences, experience_texte, zone_geo, disponibilites, statut_dispo, source, date_ajout)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Disponible', ?, ?)""",
                    (nom, prenom, telephone, email_i, type_statut, competences, experience_texte, zone_geo, disponibilites, source, datetime.date.today().isoformat())
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
                    <b>🔗 Indeed / France Travail (dépôt d'annonce)</b><br>
                    <a href="https://www.indeed.fr/jobs?q={requete_url}" target="_blank">Voir les profils similaires sur Indeed</a>
                </div>
            """, unsafe_allow_html=True)


# ============================================================
#  ONGLET 2 : MATCHING IA
# ============================================================
if onglet == "🎯 Matching IA":
    st.caption("Croise les besoins spécifiques d'un bénéficiaire avec les compétences, habilitations et la proximité des intervenants du vivier.")

    df_benef = charger_df("SELECT * FROM beneficiaires WHERE statut = 'Actif' ORDER BY nom")
    df_interv_dispo = charger_df("SELECT * FROM intervenants WHERE statut_dispo != 'Indisponible'")

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
                <b>Pathologies / besoins :</b> {benef_row['pathologies'] or 'Non renseigné'}<br>
                <b>Gestes techniques requis :</b> {benef_row['gestes_techniques'] or 'Non renseigné'}<br>
                <b>Horaires souhaités :</b> {benef_row['besoins_horaires'] or 'Non renseigné'}<br>
                <b>Niveau de dépendance :</b> {benef_row['niveau_dependance'] or 'Non renseigné'}
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
                        df_habs = charger_df("SELECT * FROM habilitations WHERE intervenant_id = ?", (int(interv["id"]),))
                        habs_txt = "; ".join([f"{h['type_habilitation']} (exp. {h['date_expiration']})" for _, h in df_habs.iterrows()]) or "Aucune habilitation enregistrée"

                        prompt = f"""
                        Tu es un coordinateur expert en aide à domicile (SAAD/SSIAD). Évalue l'adéquation entre
                        le besoin du bénéficiaire et le profil de cet intervenant.

                        CONSIGNES :
                        1. Compare les gestes techniques requis avec les compétences de l'intervenant.
                        2. Vérifie si les habilitations listées couvrent les besoins (ex : geste médical nécessitant un diplôme précis).
                        3. Repère aussi les compétences transférables issues du parcours de l'intervenant (ex : expérience en
                           EHPAD, aide-soignant, ou même un métier hors secteur impliquant patience, gestion de personnes
                           vulnérables, rigueur) qui pourraient compenser un manque d'expérience directe. Pour chaque
                           compétence transférable citée, indique de quelle expérience précise du parcours elle provient.
                           N'invente jamais une expérience absente du texte fourni.
                        4. Tiens compte de la compatibilité des disponibilités et de la zone géographique si mentionnées.

                        Renvoie STRICTEMENT un objet JSON avec les clés :
                        - 'score_competences': entier 0-100 (adéquation gestes techniques / compétences directes)
                        - 'score_habilitations': entier 0-100 (couverture des habilitations requises)
                        - 'score_global': entier 0-100 (score global pondéré)
                        - 'competences_transferables': liste de chaînes "compétence — issue de [expérience précise]"
                        - 'alerte_habilitation': texte court si une habilitation obligatoire semble manquante, sinon chaîne vide
                        - 'justification': synthèse de 2-3 lignes

                        BESOIN DU BÉNÉFICIAIRE :
                        Pathologies/besoins : {benef_row['pathologies']}
                        Gestes techniques requis : {benef_row['gestes_techniques']}
                        Horaires souhaités : {benef_row['besoins_horaires']}
                        Niveau de dépendance : {benef_row['niveau_dependance']}

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
#  ONGLET 3 : PORTEFEUILLE BÉNÉFICIAIRES
# ============================================================
if onglet == "❤️ Portefeuille Bénéficiaires":

    tab_liste_b, tab_ajout_b = st.tabs(["📋 Bénéficiaires suivis", "➕ Ajouter un bénéficiaire"])

    with tab_liste_b:
        df_b = charger_df("SELECT * FROM beneficiaires ORDER BY nom")
        st.metric("Bénéficiaires suivis", len(df_b[df_b["statut"] == "Actif"]) if not df_b.empty else 0)

        if df_b.empty:
            st.info("Aucun bénéficiaire enregistré pour l'instant.")
        else:
            for _, row in df_b.iterrows():
                couleur = "#3fae74" if row["statut"] == "Actif" else "#8996a3"
                st.markdown(f"""
                    <div class="oc-card" style="border-left-color:{couleur};">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="font-size:17px; font-weight:700;">{row['prenom']} {row['nom']}</span>
                            <span class="oc-badge" style="background-color:{couleur};">{row['statut']}</span>
                        </div>
                        <div style="color:#b8c2cc; font-size:13px; margin-top:4px;">{row['adresse'] or ''}</div>
                    </div>
                """, unsafe_allow_html=True)

                with st.expander(f"Détails / actions — {row['prenom']} {row['nom']}"):
                    st.write(f"**Pathologies / besoins :** {row['pathologies'] or 'Non renseigné'}")
                    st.write(f"**Gestes techniques requis :** {row['gestes_techniques'] or 'Non renseigné'}")
                    st.write(f"**Besoins horaires :** {row['besoins_horaires'] or 'Non renseigné'}")
                    st.write(f"**Niveau de dépendance :** {row['niveau_dependance'] or 'Non renseigné'}")
                    st.write(f"**Référent famille :** {row['referent_famille'] or 'Non renseigné'}")
                    st.write(f"**Notes :** {row['notes'] or ''}")

                    col_x, col_y = st.columns(2)
                    with col_x:
                        nouveau_statut_b = st.selectbox("Statut", ["Actif", "Inactif"], index=0 if row["statut"] == "Actif" else 1, key=f"statut_b_{row['id']}")
                        if st.button("Mettre à jour le statut", key=f"maj_b_{row['id']}"):
                            executer("UPDATE beneficiaires SET statut = ? WHERE id = ?", (nouveau_statut_b, row["id"]))
                            st.success("Statut mis à jour.")
                            st.rerun()
                    with col_y:
                        if st.button("🗑️ Supprimer ce bénéficiaire", key=f"del_b_{row['id']}"):
                            executer("DELETE FROM beneficiaires WHERE id = ?", (row["id"],))
                            st.warning("Bénéficiaire supprimé.")
                            st.rerun()

    with tab_ajout_b:
        st.subheader("Ajouter un bénéficiaire")
        with st.form("form_ajout_beneficiaire"):
            col1, col2 = st.columns(2)
            with col1:
                nom_b = st.text_input("Nom")
                prenom_b = st.text_input("Prénom")
                adresse_b = st.text_input("Adresse")
                telephone_b = st.text_input("Téléphone")
                niveau_dep = st.selectbox("Niveau de dépendance (GIR ou équivalent)", ["GIR 1", "GIR 2", "GIR 3", "GIR 4", "GIR 5", "GIR 6", "Non évalué"])
            with col2:
                pathologies = st.text_area("Pathologies / besoins particuliers")
                gestes = st.text_area("Gestes techniques requis", placeholder="Ex : aide à la toilette, transfert avec lève-personne, aide au repas...")
                horaires = st.text_input("Besoins horaires", placeholder="Ex : matin 8h-9h30, soir 19h-20h")
                referent = st.text_input("Référent famille (nom + téléphone)")
                notes_b = st.text_area("Notes complémentaires")

            submit_b = st.form_submit_button("Ajouter le bénéficiaire")
            if submit_b and nom_b and prenom_b:
                executer(
                    """INSERT INTO beneficiaires (nom, prenom, adresse, telephone, niveau_dependance, pathologies, gestes_techniques, besoins_horaires, referent_famille, notes, statut, date_creation)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Actif', ?)""",
                    (nom_b, prenom_b, adresse_b, telephone_b, niveau_dep, pathologies, gestes, horaires, referent, notes_b, datetime.date.today().isoformat())
                )
                st.success(f"{prenom_b} {nom_b} ajouté(e) au portefeuille.")
                st.rerun()


# ============================================================
#  ONGLET 4 : DOCUMENTS & TRANSMISSIONS
# ============================================================
if onglet == "📝 Documents & Transmissions":
    st.caption("Assistant de rédaction de comptes-rendus, fiches de liaison et documents professionnels — à relire avant diffusion.")

    df_benef2 = charger_df("SELECT * FROM beneficiaires WHERE statut = 'Actif' ORDER BY nom")
    df_interv2 = charger_df("SELECT * FROM intervenants ORDER BY nom")

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
                        """INSERT INTO documents_transmissions (beneficiaire_id, intervenant_id, date_creation, type_document, contenu)
                           VALUES (?, ?, ?, ?, ?)""",
                        (benef_labels2[benef_choisi2], interv_labels2[interv_choisi2], datetime.date.today().isoformat(), type_doc, texte_final)
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
            ORDER BY d.date_creation DESC
        """)
        if df_docs.empty:
            st.caption("Aucun document enregistré pour l'instant.")
        else:
            st.dataframe(df_docs, use_container_width=True, hide_index=True)


# ============================================================
#  ONGLET 5 : PLANNINGS, TOURNÉES & URGENCES
# ============================================================
if onglet == "📅 Plannings, Tournées & Urgences":

    tab_planning, tab_urgence = st.tabs(["📅 Planning", "🚨 Remplacement d'urgence"])

    df_benef3 = charger_df("SELECT * FROM beneficiaires WHERE statut = 'Actif' ORDER BY nom")
    df_interv3 = charger_df("SELECT * FROM intervenants ORDER BY nom")

    with tab_planning:
        st.subheader("Planifier une intervention")
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
                submit_p = st.form_submit_button("Planifier")

                if submit_p:
                    executer(
                        """INSERT INTO interventions (beneficiaire_id, intervenant_id, date_intervention, heure_debut, heure_fin, type_intervention, statut, notes)
                           VALUES (?, ?, ?, ?, ?, ?, 'Planifié', ?)""",
                        (benef_labels3[benef_p], interv_labels3[interv_p], date_p.isoformat(), heure_debut_p.strftime("%H:%M"), heure_fin_p.strftime("%H:%M"), type_interv_p, notes_p)
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
            WHERE i.date_intervention >= ?
            ORDER BY i.date_intervention, i.heure_debut
        """, (datetime.date.today().isoformat(),))

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
                    if row["statut"] not in ["Urgence à pourvoir", "Annulé"]:
                        if st.button("🚨 Absence", key=f"absence_{row['id']}"):
                            executer("UPDATE interventions SET statut = 'Urgence à pourvoir' WHERE id = ?", (row["id"],))
                            st.rerun()

    with tab_urgence:
        st.subheader("🚨 Interventions à pourvoir en urgence")
        df_urgences = charger_df("""
            SELECT i.id, i.date_intervention, i.heure_debut, i.heure_fin, i.type_intervention,
                   b.id as beneficiaire_id, b.prenom || ' ' || b.nom as beneficiaire,
                   b.pathologies, b.gestes_techniques, b.zone_geo_dummy
            FROM interventions i
            LEFT JOIN beneficiaires b ON i.beneficiaire_id = b.id
            WHERE i.statut = 'Urgence à pourvoir'
            ORDER BY i.date_intervention, i.heure_debut
        """.replace(", b.zone_geo_dummy", ""))

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

                if st.button(f"🔎 Trouver un remplaçant disponible", key=f"find_{urg['id']}"):
                    df_dispo = charger_df("SELECT * FROM intervenants WHERE statut_dispo = 'Disponible'")
                    if df_dispo.empty:
                        st.warning("Aucun intervenant disponible actuellement dans le vivier.")
                    else:
                        st.markdown("**Intervenants disponibles suggérés (à contacter par ordre de pertinence) :**")
                        for _, cand in df_dispo.iterrows():
                            col_c1, col_c2 = st.columns([3, 1])
                            with col_c1:
                                st.write(f"👤 **{cand['prenom']} {cand['nom']}** — {cand['zone_geo'] or 'zone non précisée'} — {cand['competences'] or ''}")
                            with col_c2:
                                if cand["email"] and st.session_state.get("mail_config", {}).get("email"):
                                    if st.button("📧 Solliciter", key=f"solliciter_{urg['id']}_{cand['id']}"):
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

                if st.button("✅ Marquer comme pourvue", key=f"resolu_{urg['id']}"):
                    executer("UPDATE interventions SET statut = 'Planifié' WHERE id = ?", (urg["id"],))
                    st.rerun()


# ============================================================
#  ONGLET 6 : CONFORMITÉ & SUIVI
# ============================================================
if onglet == "✅ Conformité & Suivi":

    tab_suivi, tab_ajout_hab = st.tabs(["📋 Suivi des habilitations", "➕ Ajouter une habilitation"])

    df_interv4 = charger_df("SELECT * FROM intervenants ORDER BY nom")

    with tab_suivi:
        aujourdhui = datetime.date.today()
        seuil_alerte = aujourdhui + datetime.timedelta(days=60)

        df_habs_all = charger_df("""
            SELECT h.id, h.type_habilitation, h.date_obtention, h.date_expiration,
                   v.prenom || ' ' || v.nom as intervenant, v.id as intervenant_id
            FROM habilitations h
            LEFT JOIN intervenants v ON h.intervenant_id = v.id
            ORDER BY h.date_expiration
        """)

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
                        "INSERT INTO habilitations (intervenant_id, type_habilitation, date_obtention, date_expiration) VALUES (?, ?, ?, ?)",
                        (interv_labels4[interv_hab], type_hab, date_obt.isoformat(), date_exp.isoformat())
                    )
                    st.success("Habilitation ajoutée.")
                    st.rerun()


# ============================================================
#  ONGLET 7 : ADMINISTRATION & PARAMÈTRES
# ============================================================
if onglet == "🛠️ Administration & Paramètres":

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

    st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)

    st.subheader("🗄️ État de la base de données")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Bénéficiaires", len(charger_df("SELECT id FROM beneficiaires")))
    col2.metric("Intervenants", len(charger_df("SELECT id FROM intervenants")))
    col3.metric("Interventions", len(charger_df("SELECT id FROM interventions")))
    col4.metric("Documents", len(charger_df("SELECT id FROM documents_transmissions")))

    st.caption(f"Base de données locale : `{DB_NAME}` (SQLite, mode WAL). Pensez à ne jamais versionner ce fichier sur GitHub (voir .gitignore).")

    st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
    st.subheader("🔐 Sécurité")
    st.write("- Mots de passe utilisateurs hachés avec **bcrypt**.")
    st.write("- Clé API Gemini chargée uniquement via les secrets Streamlit (jamais en dur dans le code).")
    st.write(f"- Statut clé Gemini : {'✅ Configurée' if IA_DISPONIBLE else '❌ Non configurée'}")
