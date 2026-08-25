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
            "type": str(row.get("type_habilitation", "")),
            "date_expiration": str(row.get("date_expiration", "")),
            "valide": bool(valid),
        })
    return out


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
    score_zone = _similarity(zone_b, zone_i) if zone_b and zone_i else 50
    horaires_b = str(benef.get("besoins_horaires", "") or "")
    dispo_i = str(interv.get("disponibilites", "") or "")
    score_dispo = _similarity(horaires_b, dispo_i) if horaires_b and dispo_i else 50
    if not habilitations:
        score_hab = 50
    else:
        score_hab = round(100 * sum(1 for h in habilitations if h["valide"]) / len(habilitations))

    score_pratique = round(score_zone * 0.65 + score_dispo * 0.35)
    # Les gestes techniques dominent. Les habilitations globales restent informatives :
    # les habilitations obligatoires d'une mission sont filtrées dans replacement_service.
    score_objectif = round(score_comp * 0.70 + score_pratique * 0.25 + score_hab * 0.05)
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
    intervenants = _dedupe_intervenants(intervenants)
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
    ai_enabled_for_run = True
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
            "score_empathie": None,
            "score_soft_skills": None,
            "ai_used": False,
            "profil_humain": "Classement métier calculé par OmniCoord.",
            "traits_dominants": [],
            "competences_transferables": [],
            "alerte_habilitation": "",
            "alerte_humaine": "",
            "justification": "Préclassement objectif calculé par OmniCoord.",
        }

        if rank < ai_top_k and ai_enabled_for_run:
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
            ai = appel_ia(prompt)
            ai_used = bool(ai)
            if not ai_used:
                # Une panne du fournisseur ne doit pas consommer 5 quotas ni
                # produire 5 erreurs identiques pendant le même matching.
                ai_enabled_for_run = False
            ai = ai or {}
            for key in ("profil_humain", "traits_dominants", "competences_transferables", "alerte_humaine", "justification"):
                if key in ai:
                    result[key] = ai[key]
            for key in ("score_empathie", "score_soft_skills"):
                if key in ai:
                    try:
                        result[key] = max(0, min(100, int(ai[key])))
                    except (TypeError, ValueError):
                        pass
        else:
            ai_used = False

        result["ai_used"] = ai_used

        # Le score final est TOUJOURS calculé en Python. En mode dégradé,
        # le classement reste 100 % objectif au lieu d'injecter de faux 50/100 IA.
        if ai_used:
            empathie = result.get("score_empathie")
            soft_skills = result.get("score_soft_skills")
            # Une réponse IA partielle ne crée pas de pseudo-note : les
            # dimensions absentes sont simplement ignorées et le poids est
            # renormalisé sur les données réellement disponibles.
            components = [(item["score_objectif"], 0.70)]
            if isinstance(empathie, int):
                components.append((empathie, 0.15))
            if isinstance(soft_skills, int):
                components.append((soft_skills, 0.15))
            denom = sum(weight for _, weight in components)
            result["score_global"] = round(sum(value * weight for value, weight in components) / denom)
        else:
            result["score_global"] = item["score_objectif"]
        enriched.append(result)

    return sorted(enriched, key=lambda x: x["score_global"], reverse=True)
