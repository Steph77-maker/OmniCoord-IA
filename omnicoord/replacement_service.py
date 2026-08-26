"""Moteur métier de remplacement d'une intervention.

Règles bloquantes déterministes : conflit de planning et habilitations exigées.
L'IA n'est pas utilisée pour décider de l'éligibilité. Les alertes de conformité
restent visibles sans retirer la personne du vivier.
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from typing import Iterable

import pandas as pd

from .compliance import canonical_key, canonicalize_habilitation

HAB_RE = re.compile(r"\[HABILITATIONS_OBLIGATOIRES:\s*(.*?)\]", re.IGNORECASE)
WORD_RE = re.compile(r"[a-zàâäçéèêëîïôöùûüÿœ]{3,}", re.IGNORECASE)
STOP = {"avec", "dans", "pour", "sans", "chez", "cette", "être", "avoir", "domicile", "personne", "besoin"}
ACTIVE_STATUSES = {"Planifié", "Urgence à pourvoir", "Proposée", "Confirmée"}
NO_EXPIRY_DATE = dt.date(9999, 12, 31)


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


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
    clean = HAB_RE.sub("", str(notes or "")).strip()
    values = [canonicalize_habilitation(x) for x in required if str(x).strip()]
    if not values:
        return clean
    return f"{clean}\n[HABILITATIONS_OBLIGATOIRES: {' | '.join(values)}]".strip()


def required_habilitations(intervention: dict) -> list[str]:
    match = HAB_RE.search(str(intervention.get("notes", "") or ""))
    return [canonicalize_habilitation(x.strip()) for x in match.group(1).split("|") if x.strip()] if match else []


def visible_notes(intervention: dict) -> str:
    return HAB_RE.sub("", str(intervention.get("notes", "") or "")).strip()


def _words(value) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(str(value or "")) if w.lower() not in STOP}


def _skill_score(intervention: dict, beneficiary: dict, candidate: dict) -> int:
    # Priorité aux gestes techniques explicitement requis ; le texte récurrent
    # n'intervient qu'en complément. Cela évite de diluer la toilette/le lever
    # dans de longues notes bénéficiaire.
    required_text = " ".join(str(x or "") for x in (
        beneficiary.get("gestes_techniques"),
        intervention.get("type_intervention"),
    ))
    req = _words(required_text)
    if not req:
        req = _words(beneficiary.get("besoins_recurrents"))
    candidate_words = _words(" ".join(str(candidate.get(k, "") or "") for k in ("competences", "experience_texte")))
    if not req:
        return 60
    return max(0, min(100, round(100 * len(req & candidate_words) / len(req))))


def _zone_score(beneficiary: dict, candidate: dict) -> int:
    address = _norm(beneficiary.get("adresse"))
    zone = _norm(candidate.get("zone_geo"))
    if not address or not zone:
        return 60
    if zone in address or address in zone:
        return 100
    a, z = set(address.split()), set(zone.split())
    common = a & z
    if not common:
        return 35
    # même ville mais secteur différent : bon score, sans être parfait
    return min(90, 70 + 10 * len(common))


def _candidate_habs(df_habs: pd.DataFrame, candidate_id: str) -> pd.DataFrame:
    if df_habs.empty or "intervenant_id" not in df_habs.columns:
        return pd.DataFrame()
    return df_habs[df_habs["intervenant_id"].astype(str) == str(candidate_id)].copy()


def evaluate_habilitations(df_habs: pd.DataFrame, required: list[str], *, warning_days: int = 60) -> tuple[list[str], list[str]]:
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
        normalized = df_habs["type_habilitation"].astype(str).map(canonical_key)
        wanted = canonicalize_habilitation(wanted)
        rows = df_habs[normalized == canonical_key(wanted)]
        if rows.empty:
            blockers.append(f"Habilitation manquante : {wanted}")
            continue

        raw_exp = rows.get("date_expiration").astype(str).str[:10]
        permanent_mask = raw_exp.eq(NO_EXPIRY_DATE.isoformat()) | rows.get("date_expiration").isna()
        parse_values = rows.get("date_expiration").mask(permanent_mask)
        expirations = pd.to_datetime(parse_values, errors="coerce")
        today_ts = pd.Timestamp(today)
        open_valid = rows[permanent_mask]
        dated_valid = rows[expirations.notna() & (expirations >= today_ts)]
        expired = rows[expirations.notna() & (expirations < today_ts)]

        if open_valid.empty and dated_valid.empty:
            nearest_expired = None
            if not expired.empty:
                nearest_expired = max(pd.to_datetime(expired["date_expiration"], errors="coerce").dt.date)
            if nearest_expired:
                blockers.append(f"Habilitation expirée : {wanted} depuis le {nearest_expired.strftime('%d/%m/%Y')}")
            else:
                blockers.append(f"Habilitation expirée : {wanted}")
            continue

        if not dated_valid.empty:
            nearest = min(pd.to_datetime(dated_valid["date_expiration"], errors="coerce").dt.date)
            if nearest <= warning_limit:
                warnings.append(f"{wanted} valide, à renouveler avant le {nearest.strftime('%d/%m/%Y')}")
        # date NULL = valide sans date d'expiration : aucune alerte inutile

    return blockers, warnings


def _day_conflict(df_interventions: pd.DataFrame, intervention: dict, candidate_id: str) -> bool:
    if df_interventions.empty:
        return False
    rows = df_interventions.copy()
    date_value = str(intervention.get("date_intervention", ""))[:10]
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


def _identity_key(c: dict) -> str:
    email = _norm(c.get("email"))
    phone = re.sub(r"\D+", "", str(c.get("telephone") or ""))
    if email:
        return f"email:{email}"
    if phone:
        return f"phone:{phone}"
    return f"name:{_norm(c.get('prenom'))}:{_norm(c.get('nom'))}"


def _prefer_result(old: dict, new: dict) -> dict:
    # En cas de doublon de fiche, ne jamais afficher la même personne dans les
    # deux groupes. On privilégie l'éligible, puis le score. Si les deux sont
    # bloqués, une raison "expirée" est plus informative que "manquante".
    if bool(new.get("eligible")) != bool(old.get("eligible")):
        return new if new.get("eligible") else old
    if new.get("eligible"):
        return new if int(new.get("score_global", 0)) > int(old.get("score_global", 0)) else old
    old_text = " ".join(old.get("blockers") or []).lower()
    new_text = " ".join(new.get("blockers") or []).lower()
    if "expir" in new_text and "expir" not in old_text:
        return new
    return old


def rank_replacements(structure_id: str, intervention: dict, beneficiary: dict, intervenants: pd.DataFrame) -> list[dict]:
    if intervenants.empty:
        return []
    from .database import sb_select
    df_habs = sb_select("habilitations", {"structure_id": structure_id}, strict=True)
    df_all = sb_select("interventions", {"structure_id": structure_id}, strict=True)
    required = required_habilitations(intervention)
    by_identity: dict[str, dict] = {}

    for _, row in intervenants.iterrows():
        c = row.to_dict()
        if c.get("deleted_at") not in (None, "", pd.NaT) and not pd.isna(c.get("deleted_at")):
            continue
        cid = str(c.get("id", ""))
        blockers: list[str] = []
        warnings: list[str] = []
        if str(c.get("statut_dispo", "")) == "Indisponible":
            blockers.append("Intervenant indiqué indisponible")
        if _day_conflict(df_all, intervention, cid):
            blockers.append("Conflit de planning sur ce créneau")
        hab_blockers, hab_warnings = evaluate_habilitations(_candidate_habs(df_habs, cid), required)
        blockers.extend(hab_blockers)
        warnings.extend(hab_warnings)

        score_comp = _skill_score(intervention, beneficiary, c)
        score_zone = _zone_score(beneficiary, c)
        daily_load = 0
        if not df_all.empty and "intervenant_id" in df_all.columns and "date_intervention" in df_all.columns:
            mask = ((df_all["intervenant_id"].astype(str) == cid)
                    & (df_all["date_intervention"].astype(str).str[:10] == str(intervention.get("date_intervention", ""))[:10]))
            daily_load = int(mask.sum())
        score_charge = max(40, 100 - daily_load * 15)
        score = round(score_comp * 0.65 + score_zone * 0.25 + score_charge * 0.10)
        if blockers:
            score = 0

        result = {
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
        }
        key = _identity_key(c)
        by_identity[key] = _prefer_result(by_identity[key], result) if key in by_identity else result

    return sorted(by_identity.values(), key=lambda x: (x["eligible"], x["score_global"]), reverse=True)


def validate_replacement_candidate(structure_id: str, intervention: dict, candidate_id: str) -> tuple[bool, list[str], list[str]]:
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
