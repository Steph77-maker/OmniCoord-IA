"""Moteur de matching hybride : règles/score Python puis IA qualitative."""
from __future__ import annotations

import datetime as dt
import re
from typing import Iterable

import pandas as pd

from .ai_service import appel_ia
from .database import sb_select

WORD_RE = re.compile(r"[a-zàâäçéèêëîïôöùûüÿœ]{3,}", re.IGNORECASE)
STOP = {"avec", "dans", "pour", "sans", "chez", "cette", "être", "avoir", "besoin", "besoins", "aide", "domicile"}


def _words(value) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(str(value or "")) if w.lower() not in STOP}


def _similarity(a, b) -> int:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 50
    inter = len(wa & wb)
    return max(0, min(100, round(100 * inter / max(1, len(wa)))))


def _valid_habilitations(df: pd.DataFrame, intervenant_id: str) -> list[dict]:
    if df.empty:
        return []
    rows = df[df["intervenant_id"].astype(str) == str(intervenant_id)].copy()
    if rows.empty:
        return []
    today = dt.date.today()
    out = []
    for _, row in rows.iterrows():
        exp = pd.to_datetime(row.get("date_expiration"), errors="coerce")
        valid = pd.isna(exp) or exp.date() >= today
        out.append({
            "type": str(row.get("type_habilitation", "")),
            "date_expiration": str(row.get("date_expiration", "")),
            "valide": bool(valid),
        })
    return out


def _base_score(benef: dict, interv: dict, habilitations: list[dict]) -> dict:
    besoins = " ".join(str(benef.get(k, "") or "") for k in (
        "besoins_recurrents", "gestes_techniques", "pathologies", "notes"
    ))
    competences = " ".join(str(interv.get(k, "") or "") for k in (
        "competences", "experience_texte"
    ))
    score_comp = _similarity(besoins, competences)
    zone_b = str(benef.get("adresse", "") or "")
    zone_i = str(interv.get("zone_geo", "") or "")
    score_zone = _similarity(zone_b, zone_i) if zone_b and zone_i else 50
    horaires_b = str(benef.get("besoins_horaires", "") or "")
    dispo_i = str(interv.get("disponibilites", "") or "")
    score_dispo = _similarity(horaires_b, dispo_i) if horaires_b and dispo_i else 50
    if not habilitations:
        score_hab = 50
    else:
        score_hab = round(100 * sum(1 for h in habilitations if h["valide"]) / len(habilitations))
    score_pratique = round((score_zone + score_dispo) / 2)
    # Score objectif avant IA. Les dimensions humaines restent à l'IA.
    score_objectif = round(score_comp * 0.50 + score_hab * 0.30 + score_pratique * 0.20)
    return {
        "score_competences": score_comp,
        "score_habilitations": score_hab,
        "score_compatibilite": score_pratique,
        "score_objectif": score_objectif,
    }


def _split_experience(value: str) -> tuple[str, str]:
    text = str(value or "")
    marker = "[SOFT SKILLS / PERSONNALITÉ]"
    if marker not in text:
        return text.strip(), ""
    left, right = text.split(marker, 1)
    return left.strip(), right.replace(":", "", 1).strip()


def match_beneficiary(
    structure_id: str,
    beneficiary: dict,
    intervenants: pd.DataFrame,
    *,
    ai_top_k: int = 5,
) -> list[dict]:
    """Préclasse tous les candidats en Python puis appelle l'IA seulement sur le TOP K."""
    if intervenants.empty:
        return []
    habs_all = sb_select("habilitations", {"structure_id": structure_id}, strict=True)
    candidates = []
    for _, row in intervenants.iterrows():
        interv = row.to_dict()
        if str(interv.get("statut_dispo", "")) == "Indisponible":
            continue
        habs = _valid_habilitations(habs_all, str(interv.get("id")))
        base = _base_score(beneficiary, interv, habs)
        candidates.append({"interv": interv, "habilitations": habs, **base})

    candidates.sort(key=lambda x: x["score_objectif"], reverse=True)
    enriched = []
    for rank, item in enumerate(candidates):
        interv = item["interv"]
        exp, soft = _split_experience(interv.get("experience_texte", ""))
        habs_txt = "; ".join(
            f"{h['type']} (exp. {h['date_expiration'] or 'NC'}, {'valide' if h['valide'] else 'expirée'})"
            for h in item["habilitations"]
        ) or "Aucune habilitation enregistrée"

        result = {
            "intervenant_id": str(interv.get("id", "")),
            "intervenant_nom": f"{interv.get('prenom', '')} {interv.get('nom', '')}".strip(),
            "intervenant_statut": interv.get("type_statut", ""),
            "intervenant_zone": interv.get("zone_geo", ""),
            "intervenant_dispo": interv.get("disponibilites", ""),
            "score_competences": item["score_competences"],
            "score_habilitations": item["score_habilitations"],
            "score_compatibilite": item["score_compatibilite"],
            "score_empathie": 50,
            "score_soft_skills": 50,
            "profil_humain": "Analyse qualitative non exécutée.",
            "traits_dominants": [],
            "competences_transferables": [],
            "alerte_habilitation": "",
            "alerte_humaine": "",
            "justification": "Préclassement objectif calculé par OmniCoord.",
        }

        if rank < ai_top_k:
            prompt = f"""
Tu es un assistant d'aide à la décision pour la coordination de services à domicile.
N'établis aucun diagnostic psychologique ou médical. N'invente aucune information.
Le moteur Python a déjà calculé les dimensions objectives ; ne les remplace pas.

BÉNÉFICIAIRE
Dépendance : {beneficiary.get('niveau_dependance', 'Non renseignée')}
Besoins : {beneficiary.get('besoins_recurrents', 'Non renseignés')}
Gestes techniques : {beneficiary.get('gestes_techniques', 'Non renseignés')}
Horaires : {beneficiary.get('besoins_horaires', 'Non renseignés')}
Notes : {beneficiary.get('notes', '')}

INTERVENANT
Parcours : {exp or 'Non renseigné'}
Observations professionnelles / savoir-être : {soft or 'Non renseignées'}
Compétences : {interv.get('competences', '')}
Habilitations : {habs_txt}

SCORES OBJECTIFS DÉJÀ CALCULÉS PAR PYTHON
Compétences : {item['score_competences']}
Habilitations enregistrées : {item['score_habilitations']}
Compatibilité pratique : {item['score_compatibilite']}

Réponds UNIQUEMENT en JSON :
{{
  "score_empathie": <0-100>,
  "score_soft_skills": <0-100>,
  "profil_humain": "<1 phrase prudente fondée sur les données>",
  "traits_dominants": ["<max 3 observations professionnelles>"],
  "competences_transferables": ["<max 3 éléments>"] ,
  "alerte_humaine": "<alerte ou chaîne vide>",
  "justification": "<2-4 phrases factuelles>"
}}
"""
            ai = appel_ia(prompt) or {}
            for key in ("profil_humain", "traits_dominants", "competences_transferables", "alerte_humaine", "justification"):
                if key in ai:
                    result[key] = ai[key]
            for key in ("score_empathie", "score_soft_skills"):
                if key in ai:
                    try:
                        result[key] = max(0, min(100, int(ai[key])))
                    except (TypeError, ValueError):
                        pass

        # Le score final est TOUJOURS calculé en Python.
        result["score_global"] = round(
            result["score_competences"] * 0.25
            + result["score_habilitations"] * 0.20
            + result["score_empathie"] * 0.25
            + result["score_soft_skills"] * 0.20
            + result["score_compatibilite"] * 0.10
        )
        enriched.append(result)

    return sorted(enriched, key=lambda x: x["score_global"], reverse=True)
