"""Moteur métier de remplacement d'une intervention.

Les règles bloquantes restent déterministes : conflit de planning et habilitations
explicitement requises par la mission. L'IA n'est jamais nécessaire pour décider
si une personne est affectable.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Iterable

import pandas as pd


HAB_MARKER = "[HABILITATIONS_OBLIGATOIRES:"
HAB_RE = re.compile(r"\[HABILITATIONS_OBLIGATOIRES:\s*(.*?)\]", re.IGNORECASE)
WORD_RE = re.compile(r"[a-zàâäçéèêëîïôöùûüÿœ]{3,}", re.IGNORECASE)
STOP = {"avec", "dans", "pour", "sans", "chez", "cette", "être", "avoir", "aide", "domicile"}
ACTIVE_STATUSES = {"Planifié", "Urgence à pourvoir", "Proposée", "Confirmée"}


def _to_time(value) -> dt.time:
    if isinstance(value, dt.time):
        return value
    text = str(value or "").strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return dt.datetime.strptime(text, fmt).time()
        except ValueError:
            pass
    raise ValueError(f"Heure invalide : {text}")


def _overlaps(start_a, end_a, start_b, end_b) -> bool:
    sa, ea, sb, eb = map(_to_time, (start_a, end_a, start_b, end_b))
    return sa < eb and sb < ea


def encode_required_habilitations(notes: str, required: Iterable[str]) -> str:
    """Conserve les notes libres et ajoute un marqueur métier rétrocompatible."""
    clean = HAB_RE.sub("", str(notes or "")).strip()
    values = [str(x).strip() for x in required if str(x).strip()]
    if not values:
        return clean
    marker = f"[HABILITATIONS_OBLIGATOIRES: {' | '.join(values)}]"
    return f"{clean}\n{marker}".strip()


def required_habilitations(intervention: dict) -> list[str]:
    match = HAB_RE.search(str(intervention.get("notes", "") or ""))
    if not match:
        return []
    return [x.strip() for x in match.group(1).split("|") if x.strip()]


def visible_notes(intervention: dict) -> str:
    return HAB_RE.sub("", str(intervention.get("notes", "") or "")).strip()


def _words(value) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(str(value or "")) if w.lower() not in STOP}


def _similarity(a, b, *, default: int = 50) -> int:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return default
    return max(0, min(100, round(100 * len(wa & wb) / max(1, len(wa)))))


def _candidate_habs(df_habs: pd.DataFrame, candidate_id: str) -> pd.DataFrame:
    if df_habs.empty or "intervenant_id" not in df_habs.columns:
        return pd.DataFrame()
    return df_habs[df_habs["intervenant_id"].astype(str) == str(candidate_id)].copy()


def evaluate_habilitations(df_habs: pd.DataFrame, required: list[str], *, warning_days: int = 60) -> tuple[list[str], list[str]]:
    """Retourne (blocages, alertes). Seules les habilitations explicitement requises bloquent."""
    if not required:
        return [], []
    today = dt.date.today()
    warning_limit = today + dt.timedelta(days=warning_days)
    blockers: list[str] = []
    warnings: list[str] = []

    for wanted in required:
        if df_habs.empty:
            blockers.append(f"Habilitation manquante : {wanted}")
            continue
        rows = df_habs[df_habs["type_habilitation"].astype(str).str.casefold() == wanted.casefold()]
        if rows.empty:
            blockers.append(f"Habilitation manquante : {wanted}")
            continue
        expirations = pd.to_datetime(rows.get("date_expiration"), errors="coerce")
        # Sans date d'expiration, on considère l'enregistrement valide mais à vérifier.
        valid_open = rows[expirations.isna()]
        valid_dated = rows[expirations.notna() & (expirations.dt.date >= today)]
        if valid_open.empty and valid_dated.empty:
            blockers.append(f"Habilitation expirée : {wanted}")
            continue
        if not valid_dated.empty:
            nearest = min(expirations[valid_dated.index].dt.date)
            if nearest <= warning_limit:
                warnings.append(f"{wanted} à renouveler avant le {nearest.strftime('%d/%m/%Y')}")
        elif not valid_open.empty:
            warnings.append(f"Date d'expiration à vérifier : {wanted}")
    return blockers, warnings


def _day_conflict(df_interventions: pd.DataFrame, intervention: dict, candidate_id: str) -> bool:
    if df_interventions.empty:
        return False
    date_value = str(intervention.get("date_intervention", ""))[:10]
    rows = df_interventions.copy()
    if "intervenant_id" in rows.columns:
        rows = rows[rows["intervenant_id"].astype(str) == str(candidate_id)]
    if "date_intervention" in rows.columns:
        rows = rows[rows["date_intervention"].astype(str).str[:10] == date_value]
    if "statut" in rows.columns:
        rows = rows[rows["statut"].isin(ACTIVE_STATUSES)]
    if "id" in rows.columns and intervention.get("id"):
        rows = rows[rows["id"].astype(str) != str(intervention.get("id"))]
    for _, row in rows.iterrows():
        try:
            if _overlaps(intervention.get("heure_debut"), intervention.get("heure_fin"), row.get("heure_debut"), row.get("heure_fin")):
                return True
        except ValueError:
            continue
    return False


def rank_replacements(structure_id: str, intervention: dict, beneficiary: dict, intervenants: pd.DataFrame) -> list[dict]:
    """Classe le vivier et explique séparément les personnes non éligibles à cette mission."""
    if intervenants.empty:
        return []
    from .database import sb_select
    df_habs = sb_select("habilitations", {"structure_id": structure_id}, strict=True)
    df_all = sb_select("interventions", {"structure_id": structure_id}, strict=True)
    required = required_habilitations(intervention)
    mission_text = " ".join(str(x or "") for x in (
        intervention.get("type_intervention"),
        beneficiary.get("besoins_recurrents"),
        beneficiary.get("gestes_techniques"),
        beneficiary.get("notes"),
    ))
    beneficiary_zone = str(beneficiary.get("adresse", "") or "")
    out: list[dict] = []

    for _, row in intervenants.iterrows():
        c = row.to_dict()
        cid = str(c.get("id", ""))
        blockers: list[str] = []
        warnings: list[str] = []
        if str(c.get("statut_dispo", "")) == "Indisponible":
            blockers.append("Intervenant indiqué indisponible")
        if _day_conflict(df_all, intervention, cid):
            blockers.append("Conflit de planning sur ce créneau")

        c_habs = _candidate_habs(df_habs, cid)
        hab_blockers, hab_warnings = evaluate_habilitations(c_habs, required)
        blockers.extend(hab_blockers)
        warnings.extend(hab_warnings)

        skills = " ".join(str(c.get(k, "") or "") for k in ("competences", "experience_texte"))
        score_comp = _similarity(mission_text, skills)
        score_zone = _similarity(beneficiary_zone, c.get("zone_geo", ""), default=60)
        # Une charge quotidienne faible sert de léger départage, sans exclure.
        daily_load = 0
        if not df_all.empty and "intervenant_id" in df_all.columns and "date_intervention" in df_all.columns:
            mask = (
                (df_all["intervenant_id"].astype(str) == cid)
                & (df_all["date_intervention"].astype(str).str[:10] == str(intervention.get("date_intervention", ""))[:10])
            )
            daily_load = int(mask.sum())
        score_charge = max(40, 100 - daily_load * 15)
        score = round(score_comp * 0.55 + score_zone * 0.25 + score_charge * 0.20)
        if blockers:
            score = 0

        out.append({
            "intervenant_id": cid,
            "intervenant_nom": f"{c.get('prenom', '')} {c.get('nom', '')}".strip(),
            "intervenant_email": c.get("email", ""),
            "type_statut": c.get("type_statut", ""),
            "zone_geo": c.get("zone_geo", ""),
            "eligible": not blockers,
            "score_global": score,
            "score_competences": score_comp,
            "score_zone": score_zone,
            "blockers": blockers,
            "warnings": warnings,
            "required_habilitations": required,
        })

    return sorted(out, key=lambda x: (x["eligible"], x["score_global"]), reverse=True)


def validate_replacement_candidate(structure_id: str, intervention: dict, candidate_id: str) -> tuple[bool, list[str], list[str]]:
    """Recontrôle juste avant l'affectation pour éviter une décision sur des données devenues obsolètes."""
    from .database import sb_select
    df_candidate = sb_select("intervenants", {"structure_id": structure_id, "id": str(candidate_id)}, strict=True)
    if df_candidate.empty:
        return False, ["Intervenant introuvable"], []
    beneficiary = {}
    if intervention.get("beneficiaire_id"):
        df_b = sb_select("beneficiaires", {"structure_id": structure_id, "id": str(intervention.get("beneficiaire_id"))}, strict=True)
        if not df_b.empty:
            beneficiary = df_b.iloc[0].to_dict()
    result = rank_replacements(structure_id, intervention, beneficiary, df_candidate)[0]
    return bool(result["eligible"]), result["blockers"], result["warnings"]
