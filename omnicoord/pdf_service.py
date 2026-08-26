"""Génération des PDF OmniCoord."""
import datetime
from fpdf import FPDF


def _pdf_safe(value) -> str:
    """Convertit le texte en caractères sûrs pour les polices PDF core Latin-1.

    Les accents français sont conservés. Les ponctuations/emoji Unicode non pris
    en charge par Helvetica sont remplacés pour empêcher un export de planter.
    """
    text = "" if value is None else str(value)
    replacements = {
        "—": "-", "–": "-", "−": "-",
        "“": '"', "”": '"', "„": '"',
        "’": "'", "‘": "'", "…": "...",
        "•": "-", "✦": "-", "█": "#", "░": ".",
        "⚠": "ATTENTION", "💡": "INFO",
        "🛠️": "", "🎓": "", "❤️": "", "🧠": "", "📍": "",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", errors="replace").decode("latin-1")


class PDFDocument(FPDF):
    def cell(self, w=None, h=None, text="", *args, **kwargs):
        return super().cell(w, h, _pdf_safe(text), *args, **kwargs)

    def multi_cell(self, w, h=None, text="", *args, **kwargs):
        return super().multi_cell(w, h, _pdf_safe(text), *args, **kwargs)

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

def creer_pdf_transmission(
    beneficiaire_nom: str,
    intervenant_nom: str,
    date_doc: str,
    contenu: str,
    type_document: str = "Fiche de liaison",
    genere_par_ia: bool = False,
) -> bytes:
    """Génère un PDF de document/transmission.

    ``type_document`` pilote le titre réel du PDF. ``genere_par_ia`` ajoute une
    mention de transparence et une zone de validation humaine uniquement lorsque
    le texte a été rédigé ou reformulé avec l'assistance de l'IA.
    """
    pdf = PDFDocument(); pdf.add_page()
    pdf.set_font("Helvetica", "B", 12); pdf.set_text_color(20, 20, 20)
    titre = (type_document or "Document").strip()
    pdf.cell(0, 8, f"{titre} — {date_doc}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, f"Bénéficiaire : {beneficiaire_nom}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Intervenant : {intervenant_nom}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4); pdf.set_font("Helvetica", "", 10); pdf.multi_cell(0, 6, contenu)

    if genere_par_ia:
        pdf.ln(7)
        pdf.set_draw_color(180, 190, 200)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(4)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(90, 100, 110)
        pdf.multi_cell(
            0,
            4.5,
            "Document rédigé avec l'assistance d'une intelligence artificielle via OmniCoord IA, "
            "à partir des informations fournies par l'utilisateur. Le contenu doit être vérifié et "
            "validé par un professionnel avant utilisation ou diffusion.",
        )
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 8)
        pdf.multi_cell(0, 4.5, "Validation humaine : Nom / fonction ____________________    Date __________    Visa __________")

    return bytes(pdf.output())

def creer_pdf_export_rgpd(beneficiaire: dict, interventions: list, documents: list) -> bytes:
    pdf = PDFDocument(); pdf.add_page()
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, f"Dossier RGPD — {beneficiaire.get('prenom', '')} {beneficiaire.get('nom', '')}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Export généré le {datetime.date.today().strftime('%d/%m/%Y')} à la demande du titulaire.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4); pdf.set_font("Helvetica", "B", 11); pdf.cell(0, 7, "Informations personnelles", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for label, key in [("Adresse", "adresse"), ("Téléphone", "telephone"), ("Dépendance", "niveau_dependance"), ("Notes", "notes")]:
        pdf.multi_cell(0, 6, f"{label} : {beneficiaire.get(key, '—')}")
    pdf.ln(4); pdf.set_font("Helvetica", "B", 11); pdf.cell(0, 7, f"Interventions ({len(interventions)})", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for iv in interventions:
        pdf.cell(0, 5, f"{iv.get('date_intervention', '')} {iv.get('heure_debut', '')}–{iv.get('heure_fin', '')} | {iv.get('type_intervention', '')} | {iv.get('statut', '')}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4); pdf.set_font("Helvetica", "B", 11); pdf.cell(0, 7, f"Documents ({len(documents)})", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for doc in documents:
        pdf.multi_cell(0, 5, f"[{doc.get('date_creation', '')}] {doc.get('type_document', '')} : {doc.get('contenu', '')[:200]}...")
    return bytes(pdf.output())

def generer_pdf_matching(intervenant_nom, intervenant_statut, intervenant_zone, beneficiaire_nom, score_global, profil_humain, traits_dominants, dimensions, scores, competences_transferables, alerte_habilitation, alerte_humaine, justification) -> bytes:
    pdf = PDFDocument(); pdf.add_page()
    pdf.set_font("Helvetica", "B", 14); pdf.set_text_color(15, 41, 66)
    pdf.cell(0, 8, f"Rapport de matching IA — {datetime.date.today().strftime('%d/%m/%Y')}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2); pdf.set_font("Helvetica", "B", 12); pdf.set_text_color(47, 124, 246)
    pdf.cell(0, 7, f"Score global : {score_global}%", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(20, 20, 20); pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Bénéficiaire : {beneficiaire_nom}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Intervenant  : {intervenant_nom} ({intervenant_statut}) — {intervenant_zone}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    if profil_humain:
        pdf.set_font("Helvetica", "I", 10); pdf.set_text_color(80, 80, 80); pdf.multi_cell(0, 6, f"Profil humain : {profil_humain}"); pdf.ln(3)
    if traits_dominants:
        pdf.set_font("Helvetica", "B", 11); pdf.set_text_color(20, 20, 20); pdf.cell(0, 7, "Empreinte comportementale", new_x="LMARGIN", new_y="NEXT"); pdf.set_font("Helvetica", "", 10)
        for trait in traits_dominants: pdf.cell(0, 6, f"  • {trait}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11); pdf.cell(0, 7, "Évaluation par dimension", new_x="LMARGIN", new_y="NEXT"); pdf.set_font("Helvetica", "", 10)
    for cle, label, _ in dimensions:
        val = int(scores.get(cle, 0)); barre = "█" * round(val / 10) + "░" * (10 - round(val / 10))
        pdf.cell(0, 6, f"  {label.replace('🛠️','').replace('🎓','').replace('❤️','').replace('🧠','').replace('📍','').strip()} : {val}%  {barre}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    if competences_transferables:
        pdf.set_font("Helvetica", "B", 11); pdf.cell(0, 7, "Compétences transférables", new_x="LMARGIN", new_y="NEXT"); pdf.set_font("Helvetica", "", 10)
        for ct in competences_transferables: pdf.multi_cell(0, 6, f"  ✦ {ct}")
        pdf.ln(3)
    if alerte_habilitation:
        pdf.set_font("Helvetica", "B", 10); pdf.set_text_color(224, 85, 79); pdf.multi_cell(0, 6, f"⚠ Habilitation : {alerte_habilitation}"); pdf.set_text_color(20,20,20); pdf.ln(2)
    if alerte_humaine:
        pdf.set_font("Helvetica", "B", 10); pdf.set_text_color(217,154,61); pdf.multi_cell(0, 6, f"💡 Profil bénéficiaire : {alerte_humaine}"); pdf.set_text_color(20,20,20); pdf.ln(2)
    if justification:
        pdf.set_font("Helvetica", "B", 11); pdf.cell(0, 7, "Synthèse de l'analyse", new_x="LMARGIN", new_y="NEXT"); pdf.set_font("Helvetica", "", 10); pdf.multi_cell(0, 6, justification)
    pdf.ln(8); pdf.set_font("Helvetica", "I", 8); pdf.set_text_color(137,150,163)
    pdf.multi_cell(0, 5, "Ce rapport est généré automatiquement par l'IA d'OmniCoord IA. Il constitue une aide à la décision et ne se substitue pas au jugement du coordinateur.")
    return bytes(pdf.output())
