"""Règles métier du planning OmniCoord."""
from __future__ import annotations

import datetime as dt
import pandas as pd

from .database import sb_select
from .exceptions import ValidationError

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
    raise ValidationError(f"Heure invalide : {text}")


def overlaps(start_a, end_a, start_b, end_b) -> bool:
    sa, ea, sb, eb = map(_to_time, (start_a, end_a, start_b, end_b))
    return sa < eb and sb < ea


def validate_time_range(start, end) -> None:
    if _to_time(end) <= _to_time(start):
        raise ValidationError("L'heure de fin doit être après l'heure de début.")


def find_intervenant_conflicts(structure_id: str, intervenant_id: str, date_intervention,
                               heure_debut, heure_fin, *, exclude_intervention_id: str | None = None) -> pd.DataFrame:
    validate_time_range(heure_debut, heure_fin)
    date_iso = date_intervention.isoformat() if hasattr(date_intervention, "isoformat") else str(date_intervention)
    df = sb_select("interventions", {
        "structure_id": structure_id,
        "intervenant_id": str(intervenant_id),
        "date_intervention": date_iso,
    }, strict=True)
    if df.empty:
        return df
    if "statut" in df.columns:
        df = df[df["statut"].isin(ACTIVE_STATUSES)]
    if exclude_intervention_id and "id" in df.columns:
        df = df[df["id"].astype(str) != str(exclude_intervention_id)]
    if df.empty:
        return df
    mask = df.apply(lambda row: overlaps(heure_debut, heure_fin, row.get("heure_debut"), row.get("heure_fin")), axis=1)
    return df[mask]


def ensure_no_intervenant_conflict(*args, **kwargs) -> None:
    if not find_intervenant_conflicts(*args, **kwargs).empty:
        raise ValidationError("Cet intervenant a déjà une intervention sur tout ou partie de ce créneau.")


def find_duplicate_interventions(structure_id: str, beneficiaire_id: str, date_intervention,
                                 heure_debut, heure_fin, type_intervention: str) -> pd.DataFrame:
    """Détecte un doublon métier exact avant création.

    Ce garde-fou applicatif évite les doubles clics et les saisies répétées. Une
    contrainte DB pourra être ajoutée plus tard si le schéma de production est figé.
    """
    date_iso = date_intervention.isoformat() if hasattr(date_intervention, "isoformat") else str(date_intervention)
    df = sb_select("interventions", {
        "structure_id": structure_id,
        "beneficiaire_id": str(beneficiaire_id),
        "date_intervention": date_iso,
    }, strict=True)
    if df.empty:
        return df
    if "statut" in df.columns:
        df = df[df["statut"].astype(str) != "Annulé"]
    if df.empty:
        return df
    wanted_start, wanted_end = _to_time(heure_debut), _to_time(heure_fin)
    mask = df.apply(
        lambda r: _to_time(r.get("heure_debut")) == wanted_start
        and _to_time(r.get("heure_fin")) == wanted_end
        and str(r.get("type_intervention", "")).strip().casefold() == str(type_intervention).strip().casefold(),
        axis=1,
    )
    return df[mask]
