"""Moteur de matching hybride : règles/score Python puis IA qualitative."""
from __future__ import annotations

import datetime as dt
import re
from typing import Iterable

import pandas as pd

from .compliance import canonicalize_habilitation

from .ai_service import appel_ia
from .database import sb_select

WORD_RE = re.compile(r"[a-zàâäçéèêëîïôöùûüÿœ]{3,}", re.IGNORECASE)
STOP = {"avec", "dans", "pour", "sans", "chez", "cette", "être", "avoir", "besoin", "besoins", "aide", "domicile"}

# Concepts métier utilisés pour comparer des formulations proches sans laisser l'IA
# décider de l'éligibilité. Les termes restent volontairement simples et auditable.
CONCEPTS = {
    "toilette": ("toilette", "hygiène", "hygiene"),
    "lever": ("lever", "levée", "levee"),
    "coucher": ("coucher",),
    "transfert": ("transfert", "transferts"),
    "repas": ("repas", "déjeuner", "dejeuner", "dîner", "diner", "petit-déjeuner", "petit dejeuner"),
    "courses": ("course", "courses"),
    "entretien": ("entretien", "ménage", "menage"),
    "accompagnement": ("accompagnement", "accompagner"),
    "mobilite": ("mobilité", "mobilite", "déplacement", "deplacement", "déplacements", "deplacements"),
}


def _words(value) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(str(value or "")) if w.lower() not in STOP}


def _similarity(a, b) -> int:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 50
    inter = len(wa & wb)
    return max(0, min(100, round(100 * inter / max(1, len(wa)))))

def _phrases(value) -> list[str]:
    parts = re.split(r"[,;\n/]+", str(value or ""))
    return [p.strip().lower() for p in parts if p.strip()]


def _concepts(value) -> set[str]:
    text = str(value or "").lower()
    found = set()
    for concept, variants in CONCEPTS.items():
        if any(v in text for v in variants):
            found.add(concept)
    return found


def _coverage(required: set[str], offered: set[str]) -> int:
    if not required:
        return 50
    return round(100 * len(required & offered) / len(required))


def _required_skill_score(benef: dict, interv: dict) -> int:
    """Score métier : gestes explicites d'abord, besoins récurrents ensuite.

    Les gestes techniques ont plus de poids car ils représentent la contrainte
    opérationnelle la plus directement vérifiable. Les besoins récurrents
    départagent ensuite des profils ayant une couverture technique similaire.
    """
    offered_text = " ".join(str(interv.get(k, "") or "") for k in ("competences", "experience_texte"))
    offered_concepts = _concepts(offered_text)

    gestures_text = str(benef.get("gestes_techniques", "") or "")
    recurring_text = str(benef.get("besoins_recurrents", "") or "")
    gestures_concepts = _concepts(gestures_text)
    recurring_concepts = _concepts(recurring_text)

    concept_parts = []
    if gestures_concepts:
        concept_parts.append((_coverage(gestures_concepts, offered_concepts), 0.72))
    if recurring_concepts:
        concept_parts.append((_coverage(recurring_concepts, offered_concepts), 0.28 if gestures_concepts else 1.0))

    if concept_parts:
        total_weight = sum(weight for _, weight in concept_parts)
        concept_score = round(sum(score * weight for score, weight in concept_parts) / total_weight)
    else:
        concept_score = 50

    # Complément lexical pour les compétences non couvertes par le petit lexique
    # métier. Il ne peut pas renverser totalement une mauvaise couverture des
    # gestes requis.
    lexical_target = " ".join(v for v in (gestures_text, recurring_text) if v)
    lexical = _similarity(lexical_target, offered_text)
    return round(concept_score * 0.85 + lexical * 0.15)


def _dedupe_intervenants(df: pd.DataFrame) -> pd.DataFrame:
    """Évite qu'un même profil apparaisse plusieurs fois dans un matching."""
    if df.empty:
        return df
    work = df.copy()
    def norm(v):
        return re.sub(r"\s+", "", str(v or "").strip().lower())
    keys = []
    for _, row in work.iterrows():
        email = norm(row.get("email"))
        phone = re.sub(r"\D+", "", str(row.get("telephone", "") or ""))
        if email:
            key = f"email:{email}"
        elif phone:
            key = f"phone:{phone}"
        else:
            # En l'absence de coordonnées, un doublon de saisie portant le même
            # nom/prénom ne doit pas apparaître deux fois dans un matching.
            key = "name:" + "|".join([norm(row.get("prenom")), norm(row.get("nom"))])
        keys.append(key)
    work["__dedupe_key"] = keys
    return work.drop_duplicates("__dedupe_key", keep="first").drop(columns=["__dedupe_key"])


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
            "type": canonicalize_habilitation(row.get("type_habilitation", "")),
            "date_expiration": str(row.get("date_expiration", "")),
            "valide": bool(valid),
        })
    return out



def _time_minutes(hour: str, minute: str = "0") -> int:
    return int(hour) * 60 + int(minute or 0)


def _extract_time_range(value: str) -> tuple[int, int] | None:
    """Extrait le premier créneau horaire d'un texte métier (8h-17h, 10h00 à 12h00...)."""
    text = str(value or "").lower().replace("h", ":")
    matches = re.findall(r"\b([0-2]?\d)(?::([0-5]\d))?\b", text)
    if len(matches) < 2:
        return None
    start = _time_minutes(matches[0][0], matches[0][1] or "0")
    end = _time_minutes(matches[1][0], matches[1][1] or "0")
    if end <= start:
        return None
    return start, end


def _availability_score(benef_horaires: str, interv_dispo: str) -> int:
    """Score explicable : couvre le créneau demandé > chevauche > texte non interprétable."""
    requested = _extract_time_range(benef_horaires)
    offered = _extract_time_range(interv_dispo)
    if requested and offered:
        rs, re_ = requested
        os, oe = offered
        if os <= rs and oe >= re_:
            return 100
        overlap = max(0, min(re_, oe) - max(rs, os))
        requested_len = max(1, re_ - rs)
        if overlap:
            return max(30, min(95, round(100 * overlap / requested_len)))
        return 0
    if not str(benef_horaires or "").strip() or not str(interv_dispo or "").strip():
        return 50
    return _similarity(benef_horaires, interv_dispo)


def _zone_score(benef_address: str, interv_zone: str) -> int:
    """Compare les zones sans prétendre calculer un trajet réel."""
    a = _words(benef_address)
    z = _words(interv_zone)
    if not a or not z:
        return 50
    common = a & z
    if common:
        # Une ville/zone commune est déjà un signal fort ; le trajet réel viendra plus tard.
        return min(100, 80 + 10 * min(2, len(common)))
    return max(0, min(60, _similarity(benef_address, interv_zone)))


def _base_score(benef: dict, interv: dict, habilitations: list[dict]) -> dict:
    besoins = " ".join(str(benef.get(k, "") or "") for k in (
        "besoins_recurrents", "gestes_techniques", "notes"
    ))
    competences = " ".join(str(interv.get(k, "") or "") for k in (
        "competences", "experience_texte"
    ))
    score_large = _similarity(besoins, competences)
    score_gestes = _required_skill_score(benef, interv)
    score_comp = round(score_gestes * 0.75 + score_large * 0.25)

    zone_b = str(benef.get("adresse", "") or "")
    zone_i = str(interv.get("zone_geo", "") or "")
    score_zone = _zone_score(zone_b, zone_i)
    horaires_b = str(benef.get("besoins_horaires", "") or "")
    dispo_i = str(interv.get("disponibilites", "") or "")
    score_dispo = _availability_score(horaires_b, dispo_i)
    if not habilitations:
        score_hab = 50
    else:
        score_hab = round(100 * sum(1 for h in habilitations if h["valide"]) / len(habilitations))

    score_pratique = round(score_zone * 0.45 + score_dispo * 0.55)
    # Les gestes techniques dominent. Les habilitations globales restent informatives :
    # les habilitations obligatoires d'une mission sont filtrées dans replacement_service.
    score_objectif = round(score_comp * 0.72 + score_pratique * 0.25 + score_hab * 0.03)
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
    """Préclasse en Python puis enrichit le TOP K avec UN SEUL appel IA.

    Le rang et le score final restent déterministes. Gemini fournit uniquement
    une lecture qualitative explicable des meilleurs profils.
    """
    if intervenants.empty:
        return []

    intervenants = _dedupe_intervenants(intervenants)
    habs_all = sb_select("habilitations", {"structure_id": structure_id}, strict=True)
    candidates: list[dict] = []

    for _, row in intervenants.iterrows():
        interv = row.to_dict()
        if str(interv.get("statut_dispo", "")) == "Indisponible":
            continue
        habs = _valid_habilitations(habs_all, str(interv.get("id")))
        base = _base_score(beneficiary, interv, habs)
        exp, soft = _split_experience(interv.get("experience_texte", ""))
        candidates.append({
            "interv": interv,
            "habilitations": habs,
            "experience": exp,
            "soft": soft,
            **base,
        })

    candidates.sort(key=lambda x: x["score_objectif"], reverse=True)
    results: list[dict] = []

    for item in candidates:
        interv = item["interv"]
        results.append({
            "intervenant_id": str(interv.get("id", "")),
            "intervenant_nom": f"{interv.get('prenom', '')} {interv.get('nom', '')}".strip(),
            "intervenant_statut": interv.get("type_statut", ""),
            "intervenant_zone": interv.get("zone_geo", ""),
            "intervenant_dispo": interv.get("disponibilites", ""),
            "score_competences": item["score_competences"],
            "score_habilitations": item["score_habilitations"],
            "score_compatibilite": item["score_compatibilite"],
            "score_empathie": None,
            "score_soft_skills": None,
            "score_global": item["score_objectif"],
            "ai_used": False,
            "profil_humain": "Score fondé sur les données métier enregistrées.",
            "traits_dominants": [],
            "competences_transferables": [],
            "alerte_habilitation": "",
            "alerte_humaine": "",
            "justification": "Classement objectif calculé par OmniCoord.",
        })

    top_n = min(max(0, int(ai_top_k)), len(candidates))
    if top_n:
        compact_candidates = []
        for item in candidates[:top_n]:
            interv = item["interv"]
            habs_txt = "; ".join(
                f"{h['type']} ({'valide' if h['valide'] else 'expirée'}, exp. {h['date_expiration'] or 'sans date'})"
                for h in item["habilitations"]
            ) or "Aucune habilitation enregistrée"
            compact_candidates.append({
                "intervenant_id": str(interv.get("id", "")),
                "nom": f"{interv.get('prenom', '')} {interv.get('nom', '')}".strip(),
                "competences": str(interv.get("competences", "") or ""),
                "parcours": item["experience"],
                "savoir_etre_observe": item["soft"],
                "zone": str(interv.get("zone_geo", "") or ""),
                "disponibilites": str(interv.get("disponibilites", "") or ""),
                "habilitations": habs_txt,
                "scores_objectifs": {
                    "competences": item["score_competences"],
                    "habilitations": item["score_habilitations"],
                    "compatibilite_pratique": item["score_compatibilite"],
                    "score_metier": item["score_objectif"],
                },
            })

        prompt = f"""
Tu es un assistant d'aide à la décision pour la coordination de services à domicile.
Analyse en UNE SEULE réponse les profils ci-dessous. Le moteur Python a déjà classé
les candidats : tu ne dois NI modifier leur rang NI inventer des faits.
N'établis aucun diagnostic psychologique ou médical.

BÉNÉFICIAIRE
Dépendance : {beneficiary.get('niveau_dependance', 'Non renseignée')}
Besoins : {beneficiary.get('besoins_recurrents', 'Non renseignés')}
Gestes techniques : {beneficiary.get('gestes_techniques', 'Non renseignés')}
Horaires : {beneficiary.get('besoins_horaires', 'Non renseignés')}
Notes : {beneficiary.get('notes', '')}

CANDIDATS (JSON)
{compact_candidates}

Réponds UNIQUEMENT en JSON strict sous cette forme :
{{
  "analyses": [
    {{
      "intervenant_id": "<id exact>",
      "score_empathie": <0-100 ou null>,
      "score_soft_skills": <0-100 ou null>,
      "profil_humain": "<1 phrase prudente fondée sur les données>",
      "traits_dominants": ["<max 3 observations professionnelles>"],
      "competences_transferables": ["<max 3 éléments>"],
      "alerte_humaine": "<alerte factuelle ou chaîne vide>",
      "justification": "<2-4 phrases factuelles>"
    }}
  ]
}}
"""
        ai_payload = appel_ia(prompt) or {}
        analyses = ai_payload.get("analyses", []) if isinstance(ai_payload, dict) else []
        if isinstance(analyses, list):
            by_id = {str(r["intervenant_id"]): r for r in results}
            for analysis in analyses:
                if not isinstance(analysis, dict):
                    continue
                target = by_id.get(str(analysis.get("intervenant_id", "")))
                if not target:
                    continue
                target["ai_used"] = True
                for key in ("profil_humain", "traits_dominants", "competences_transferables", "alerte_humaine", "justification"):
                    if key in analysis:
                        target[key] = analysis[key]
                for key in ("score_empathie", "score_soft_skills"):
                    value = analysis.get(key)
                    if value is None:
                        continue
                    try:
                        target[key] = max(0, min(100, int(value)))
                    except (TypeError, ValueError):
                        pass

    # Le score final ne dépend jamais de Gemini : explicable, stable et auditable.
    return sorted(results, key=lambda x: x["score_global"], reverse=True)

