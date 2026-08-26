"""Normalisation métier des diplômes et habilitations OmniCoord.

Ce module centralise les libellés pour éviter que des synonymes (ex. "Diplôme AES"
et "DEAES") créent des doublons conceptuels ou des écarts de matching.
"""
from __future__ import annotations

import re
import unicodedata

CANONICAL_HABILITATIONS = [
    "DEAES",
    "PSC1 / SST",
    "Permis B",
    "Visite médecine du travail",
    "Habilitation gestes et postures",
    "AFGSU",
    "Autre",
]


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


_ALIAS_TO_CANONICAL = {
    # DEAES / AES
    "deaes": "DEAES",
    "aes": "DEAES",
    "diplome aes": "DEAES",
    "diplome d etat aes": "DEAES",
    "diplome d etat d accompagnant educatif et social": "DEAES",
    "accompagnant educatif et social": "DEAES",
    # Secourisme (conservé groupé pour compatibilité avec la base actuelle)
    "psc1": "PSC1 / SST",
    "sst": "PSC1 / SST",
    "psc1 sst": "PSC1 / SST",
    "psc1 / sst": "PSC1 / SST",
    "prevention et secours civiques de niveau 1": "PSC1 / SST",
    # Permis
    "permis b": "Permis B",
    "permis de conduire b": "Permis B",
    # Médecine du travail
    "visite medecine du travail": "Visite médecine du travail",
    "visite medicale du travail": "Visite médecine du travail",
    # Gestes et postures
    "habilitation gestes et postures": "Habilitation gestes et postures",
    "gestes et postures": "Habilitation gestes et postures",
    "formation gestes et postures": "Habilitation gestes et postures",
    # AFGSU
    "afgsu": "AFGSU",
}

# Ajoute les libellés canoniques eux-mêmes dans le dictionnaire.
for _label in CANONICAL_HABILITATIONS:
    if _label != "Autre":
        _ALIAS_TO_CANONICAL.setdefault(_norm(_label), _label)


def canonicalize_habilitation(value: object) -> str:
    """Retourne le libellé canonique connu, sinon la valeur nettoyée.

    Les valeurs inconnues ne sont pas supprimées : elles restent exploitables via
    le type "Autre" côté interface, sans perte d'information historique.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _ALIAS_TO_CANONICAL.get(_norm(raw), raw)


def canonical_key(value: object) -> str:
    """Clé stable pour comparer deux libellés métier équivalents."""
    return _norm(canonicalize_habilitation(value))


def canonicalize_many(values) -> list[str]:
    """Normalise et déduplique une séquence en conservant l'ordre."""
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        canonical = canonicalize_habilitation(value)
        key = canonical_key(canonical)
        if canonical and key not in seen:
            out.append(canonical)
            seen.add(key)
    return out


def duplicate_habilitation_reason(existing_records, requested_type: object, requested_date_obtention, *, permanent: bool) -> str | None:
    """Détecte un doublon conceptuel sans empêcher un vrai renouvellement daté.

    - un même type canonique + même date d'obtention = doublon ;
    - une habilitation déjà permanente ne doit pas être recréée ;
    - passer un type en permanent se fait par correction de la ligne existante.

    Un renouvellement avec une nouvelle date d'obtention reste autorisé afin de
    préserver l'historique métier.
    """
    import datetime as dt

    wanted_key = canonical_key(requested_type)
    if not wanted_key:
        return None
    req_date = str(requested_date_obtention or "")[:10]

    for record in existing_records or []:
        if canonical_key(record.get("type_habilitation")) != wanted_key:
            continue
        existing_obt = str(record.get("date_obtention") or "")[:10]
        exp_raw = record.get("date_expiration")
        exp_text = str(exp_raw or "")[:10]
        existing_permanent = not exp_raw or exp_text == "9999-12-31"

        if existing_permanent:
            return "Cette habilitation existe déjà comme valide sans expiration. Corrigez la ligne existante si nécessaire."
        if permanent:
            return "Cette habilitation existe déjà. Pour la rendre permanente, corrigez la ligne existante plutôt que d'en créer une seconde."
        if req_date and existing_obt == req_date:
            return "Cette habilitation existe déjà avec la même date d'obtention."

    return None
