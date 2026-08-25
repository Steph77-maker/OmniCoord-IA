"""Analyse de CV pour préremplir une fiche intervenant.

Le document est extrait localement puis le texte utile est envoyé au service IA
centralisé. Aucune écriture en base n'a lieu sans validation explicite du
coordinateur.
"""
from __future__ import annotations

import io
import re
from typing import Any

from .exceptions import ValidationError

MAX_CV_CHARS = 18000
ALLOWED_TYPES = {"application/pdf", "text/plain"}
KNOWN_HABILITATIONS = [
    "Diplôme AES",
    "DEAES",
    "PSC1 / SST",
    "Permis B",
    "Visite médecine du travail",
    "Habilitation gestes et postures",
    "AFGSU",
]


def extract_cv_text(uploaded_file) -> str:
    """Extrait du texte d'un PDF texte ou d'un .txt, avec limites de taille."""
    if uploaded_file is None:
        raise ValidationError("Ajoutez d'abord un CV.")
    data = uploaded_file.getvalue()
    if not data:
        raise ValidationError("Le fichier est vide.")
    if len(data) > 8 * 1024 * 1024:
        raise ValidationError("Le CV dépasse la taille maximale de 8 Mo.")

    mime = getattr(uploaded_file, "type", "") or ""
    name = (getattr(uploaded_file, "name", "") or "").lower()
    if mime == "text/plain" or name.endswith(".txt"):
        text = data.decode("utf-8", errors="replace")
    elif mime == "application/pdf" or name.endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValidationError("Le lecteur PDF n'est pas installé sur le serveur.") from exc
        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:20])
        except Exception as exc:
            raise ValidationError("Impossible de lire ce PDF.") from exc
    else:
        raise ValidationError("Format non pris en charge. Utilisez un PDF ou un fichier texte.")

    text = re.sub(r"\x00", "", text).strip()
    if len(text) < 40:
        raise ValidationError("Le CV ne contient pas assez de texte exploitable. Un PDF scanné peut nécessiter un OCR.")
    return text[:MAX_CV_CHARS]


def analyse_cv(uploaded_file) -> dict[str, Any] | None:
    """Retourne des champs structurés destinés à un formulaire de validation."""
    from .ai_service import appel_ia
    text = extract_cv_text(uploaded_file)
    prompt = f"""
Tu es un assistant de recrutement pour un service d'aide à domicile.
Analyse uniquement le CV ci-dessous. N'invente rien. Si une information n'est
pas explicitement présente, retourne une chaîne vide ou une liste vide.

Retourne UNIQUEMENT un JSON valide avec exactement ces clés :
nom, prenom, telephone, email, competences, experience_texte, zone_geo,
disponibilites, soft_skills, habilitations_detectees.

- competences : chaîne concise séparée par des virgules, uniquement compétences/gestes réellement mentionnés.
- experience_texte : résumé factuel du parcours en 3 à 6 lignes.
- soft_skills : uniquement qualités explicitement indiquées ou clairement étayées par le CV ; sinon chaîne vide.
- habilitations_detectees : liste contenant uniquement des valeurs parmi {KNOWN_HABILITATIONS!r} lorsqu'elles sont explicitement mentionnées.
- Ne déduis aucune habilitation à partir d'un métier ou d'une expérience.

CV :
{text}
"""
    data = appel_ia(prompt)
    if not isinstance(data, dict):
        return None
    cleaned = {k: data.get(k, "") for k in (
        "nom", "prenom", "telephone", "email", "competences", "experience_texte",
        "zone_geo", "disponibilites", "soft_skills", "habilitations_detectees",
    )}
    habs = cleaned.get("habilitations_detectees")
    if not isinstance(habs, list):
        habs = []
    cleaned["habilitations_detectees"] = [h for h in habs if h in KNOWN_HABILITATIONS]
    return cleaned
