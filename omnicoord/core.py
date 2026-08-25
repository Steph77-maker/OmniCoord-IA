"""Façade de compatibilité et configuration OmniCoord IA.

La logique a été extraite vers des modules spécialisés sans changer les appels
utilisés par l'interface pendant cette première étape de migration.
"""
import logging
import streamlit as st

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("omnicoord")

st.set_page_config(page_title="OmniCoord IA", page_icon="🩺", layout="wide", initial_sidebar_state="expanded")

CUSTOM_CSS = """
<style>
:root { --oc-navy-deep:#0a1929; --oc-navy:#0f2942; --oc-navy-panel:#132f4c; --oc-steel:#8996a3; --oc-steel-light:#b8c2cc; --oc-medical-blue:#2f7cf6; --oc-medical-blue-soft:#4c8dfa; --oc-alert:#e0554f; --oc-warning:#d99a3d; --oc-success:#3fae74; }
.stApp { background:linear-gradient(160deg,var(--oc-navy-deep) 0%,var(--oc-navy) 55%,#0d2138 100%); color:#f2f5f8 !important; }
section[data-testid="stSidebar"] { background:linear-gradient(180deg,#0c1f33 0%,#0a1929 100%); border-right:1px solid rgba(137,150,163,.25); }
p,span,label,.stMarkdown,div[data-baseweb="select"] span { color:#f2f5f8 !important; }
h1,h2,h3,h4,h5,h6 { color:#fff !important; letter-spacing:.3px; }
.oc-badge{display:inline-block;padding:4px 14px;border-radius:20px;font-weight:700;color:white}.oc-card{background:linear-gradient(135deg,var(--oc-navy-panel) 0%,#0f2438 100%);border:1px solid rgba(137,150,163,.25);border-left:4px solid var(--oc-medical-blue);border-radius:12px;padding:18px 20px;margin-bottom:14px;color:#f2f5f8!important}.oc-card-alert{border-left:4px solid var(--oc-alert)!important}.oc-card-warning{border-left:4px solid var(--oc-warning)!important}.oc-card-ok{border-left:4px solid var(--oc-success)!important}.oc-metal-divider{height:2px;background:linear-gradient(90deg,transparent,var(--oc-steel) 50%,transparent);margin:18px 0;opacity:.5}.stButton>button{background:linear-gradient(135deg,var(--oc-medical-blue) 0%,#1f5fd6 100%);color:white;border:none;border-radius:8px;font-weight:600}.stButton>button:hover{background:linear-gradient(135deg,var(--oc-medical-blue-soft) 0%,var(--oc-medical-blue) 100%);border:none;color:white}div[data-testid="stMetricValue"]{color:var(--oc-medical-blue-soft)!important}input,textarea,select{color:#fff!important}div[data-baseweb="input"]{background-color:rgba(19,47,76,.6)!important}div[data-baseweb="base-input"] input{color:#fff!important;background-color:rgba(13,33,56,.8)!important}.planning-table{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}.planning-table th{background:linear-gradient(135deg,#1a3f6f 0%,#0f2942 100%);color:#f2f5f8;padding:10px 8px;text-align:center;border:1px solid rgba(137,150,163,.3);font-weight:700}.planning-table th.col-intervenant{background:linear-gradient(135deg,#0c1f33 0%,#0a1929 100%);text-align:left;padding-left:12px;min-width:140px}.planning-table td{border:1px solid rgba(137,150,163,.2);padding:6px 4px;vertical-align:top;min-width:110px;background:rgba(10,25,41,.4)}.planning-table td.col-intervenant{background:rgba(12,31,51,.7);color:#e6ecf2;font-weight:600;padding:8px 12px;vertical-align:middle}.planning-cell{background:linear-gradient(135deg,#132f4c 0%,#0f2438 100%);border-radius:6px;padding:5px 7px;margin:2px;font-size:12px;border-left:3px solid #2f7cf6;color:#f2f5f8}.planning-cell.urgence{border-left-color:#e0554f!important}.planning-cell.realise{border-left-color:#3fae74!important}.planning-cell.annule{border-left-color:#8996a3!important;opacity:.6}.planning-empty{color:rgba(137,150,163,.3);font-size:12px;text-align:center;padding:10px 0}.alert-box{border-radius:10px;padding:14px 18px;margin-bottom:10px}.alert-box-rouge{background:rgba(224,85,79,.12);border:1px solid rgba(224,85,79,.45);border-left:4px solid #e0554f}.alert-box-orange{background:rgba(217,154,61,.12);border:1px solid rgba(217,154,61,.4);border-left:4px solid #d99a3d}.alert-box-bleu{background:rgba(47,124,246,.1);border:1px solid rgba(47,124,246,.35);border-left:4px solid #2f7cf6}.fiche-section{background:linear-gradient(135deg,#132f4c 0%,#0f2438 100%);border:1px solid rgba(137,150,163,.2);border-radius:10px;padding:16px 18px;margin-bottom:12px}.fiche-section h4{color:#4c8dfa!important;margin-bottom:10px;font-size:14px;text-transform:uppercase;letter-spacing:1px}.fiche-row{display:flex;gap:8px;margin-bottom:6px}.fiche-label{color:#8996a3;font-size:13px;min-width:140px;flex-shrink:0}.fiche-value{color:#f2f5f8;font-size:13px}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

from .database import sb, get_supabase, get_supabase_admin, sb_select, sb_insert, sb_update, sb_delete, sb_rpc, audit
from .security import date_fr, chiffrer_mdp_mail, dechiffrer_mdp_mail, h, est_bloque, enregistrer_tentative
from .auth import check_password
from .ai_service import IA_DISPONIBLE, peut_utiliser_ia, incrementer_quota_ia, appel_ia, appel_ia_texte, distance_km, classer_candidats_urgence
from .pdf_service import PDFDocument, creer_pdf_transmission, creer_pdf_export_rgpd, generer_pdf_matching
from .email_service import envoyer_email
_generer_pdf_matching = generer_pdf_matching

from .admin_service import require_platform_admin, create_auth_user, delete_auth_user
