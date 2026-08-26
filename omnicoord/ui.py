"""OmniCoord IA - interface Streamlit extraite du monolithe historique.

Cette première migration conserve volontairement le comportement existant.
"""
import datetime
import hashlib
import html
import logging
import math
import urllib.parse
import pandas as pd
import streamlit as st
from . import core
from .matching_service import match_beneficiary
from .planning_service import ensure_no_intervenant_conflict, find_duplicate_interventions
from .replacement_service import (
    NO_EXPIRY_DATE,
    encode_required_habilitations,
    required_habilitations,
    rank_replacements,
    validate_replacement_candidate,
)
from .exceptions import ValidationError, DatabaseError
from .cv_service import analyse_cv
from .compliance import CANONICAL_HABILITATIONS, canonical_key, canonicalize_habilitation, duplicate_habilitation_reason

# Aliases conservés pour limiter les changements de comportement pendant la migration.
sb = core.sb
IA_DISPONIBLE = core.IA_DISPONIBLE
get_supabase_admin = core.get_supabase_admin
create_auth_user = core.create_auth_user
delete_auth_user = core.delete_auth_user
sb_select = core.sb_select
sb_insert = core.sb_insert
sb_update = core.sb_update
sb_delete = core.sb_delete
sb_rpc = core.sb_rpc
audit = core.audit
h = core.h
date_fr = core.date_fr
peut_utiliser_ia = core.peut_utiliser_ia
appel_ia = core.appel_ia
appel_ia_texte = core.appel_ia_texte
classer_candidats_urgence = core.classer_candidats_urgence
creer_pdf_transmission = core.creer_pdf_transmission
creer_pdf_export_rgpd = core.creer_pdf_export_rgpd
_generer_pdf_matching = core._generer_pdf_matching
envoyer_email = core.envoyer_email
chiffrer_mdp_mail = core.chiffrer_mdp_mail

logger = logging.getLogger("omnicoord.ui")


def _split_experience_softskills(value):
    """Sépare le parcours des observations soft skills stockées historiquement dans le même champ."""
    text = str(value or "")
    marker = "[SOFT SKILLS / PERSONNALITÉ] :"
    if marker in text:
        parcours, soft = text.split(marker, 1)
        return parcours.strip(), soft.strip()
    return text.strip(), ""


def _experience_with_softskills(parcours, soft_skills):
    parcours = (parcours or "").strip()
    soft_skills = (soft_skills or "").strip()
    if soft_skills:
        return f"{parcours}\n\n[SOFT SKILLS / PERSONNALITÉ] : {soft_skills}".strip()
    return parcours




def _normalize_identity_text(value):
    return " ".join(str(value or "").strip().casefold().split())




def _time_minutes(value):
    """Convertit HH:MM[:SS] en minutes ; None si la valeur est inexploitable."""
    if isinstance(value, datetime.time):
        return value.hour * 60 + value.minute
    text = str(value or "").strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.datetime.strptime(text, fmt).time()
            return parsed.hour * 60 + parsed.minute
        except ValueError:
            continue
    return None


def _dashboard_latest_habilitations(df_habs):
    """Conserve l'état le plus récent par intervenant et habilitation canonique."""
    if df_habs is None or df_habs.empty:
        return pd.DataFrame() if df_habs is None else df_habs.copy()
    work = df_habs.copy()
    if "type_habilitation" not in work.columns or "intervenant_id" not in work.columns:
        return work
    work["_type_canonique"] = work["type_habilitation"].astype(str).map(canonicalize_habilitation)
    work["_date_obt_sort"] = pd.to_datetime(work.get("date_obtention"), errors="coerce")
    if "created_at" in work.columns:
        work["_created_sort"] = pd.to_datetime(work["created_at"], errors="coerce")
    else:
        work["_created_sort"] = pd.NaT
    if "id" not in work.columns:
        work["id"] = work.index.astype(str)
    work = work.sort_values(
        ["intervenant_id", "_type_canonique", "_date_obt_sort", "_created_sort", "id"],
        na_position="first",
    )
    return work.drop_duplicates(["intervenant_id", "_type_canonique"], keep="last")


def _dashboard_missing(value):
    """True pour None, NaN et chaînes vides sans considérer 0 comme manquant."""
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return not str(value).strip()


def _dashboard_plain(text):
    """Texte simple pour les prompts/empreintes, sans balises HTML."""
    import re
    return re.sub(r"<[^>]+>", "", html.unescape(str(text or ""))).strip()


def _dashboard_alert_fingerprint(groups):
    raw = "\n".join(
        f"{level}:{_dashboard_plain(item)}"
        for level, items in groups.items()
        for item in items
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _dashboard_collect_checks(SID):
    """Contrôles déterministes du centre de pilotage.

    L'IA n'intervient jamais ici : elle ne fera qu'expliquer ces résultats.
    """
    today = datetime.date.today()
    in_7d = today + datetime.timedelta(days=7)
    in_60d = today + datetime.timedelta(days=60)

    df_benef = sb_select("beneficiaires", {"structure_id": SID})
    df_interv = sb_select("intervenants", {"structure_id": SID})
    df_iv = sb_select("interventions", {"structure_id": SID})
    df_habs = sb_select("habilitations", {"structure_id": SID})

    # Ne pas faire remonter les éléments archivés comme actifs.
    if not df_benef.empty and "deleted_at" in df_benef.columns:
        df_benef = df_benef[df_benef["deleted_at"].isna()].copy()
    if not df_interv.empty and "deleted_at" in df_interv.columns:
        df_interv = df_interv[df_interv["deleted_at"].isna()].copy()

    critical, warning, info = [], [], []
    counters = {
        "beneficiaires_actifs": 0,
        "intervenants_dispo": 0,
        "interventions_7j": 0,
        "urgences": 0,
    }

    if not df_benef.empty:
        if "statut" in df_benef.columns:
            counters["beneficiaires_actifs"] = int((df_benef["statut"].astype(str) == "Actif").sum())
        else:
            counters["beneficiaires_actifs"] = len(df_benef)
    if not df_interv.empty:
        counters["intervenants_dispo"] = int((df_interv.get("statut_dispo", pd.Series(dtype=str)).astype(str) == "Disponible").sum())

    interv_by_id = {
        str(r.get("id")): {
            "nom": f"{r.get('prenom', '')} {r.get('nom', '')}".strip() or "Intervenant inconnu",
            "dispo": str(r.get("statut_dispo", "") or ""),
            "row": r,
        }
        for _, r in df_interv.iterrows()
    } if not df_interv.empty else {}
    benef_by_id = {
        str(r.get("id")): f"{r.get('prenom', '')} {r.get('nom', '')}".strip() or "Bénéficiaire inconnu"
        for _, r in df_benef.iterrows()
    } if not df_benef.empty else {}

    # État courant des habilitations, pas l'historique complet.
    latest_habs = _dashboard_latest_habilitations(df_habs)
    if not latest_habs.empty:
        for _, hb in latest_habs.iterrows():
            iid = str(hb.get("intervenant_id", ""))
            if iid and iid not in interv_by_id:
                continue
            exp_raw = hb.get("date_expiration")
            exp_dt = pd.to_datetime(exp_raw, errors="coerce")
            if pd.isna(exp_dt):
                continue
            exp = exp_dt.date()
            # 9999-12-31 = valeur sentinelle "sans expiration".
            if exp >= datetime.date(9999, 1, 1):
                continue
            nom = h(interv_by_id.get(iid, {}).get("nom", "Inconnu"))
            typ = h(canonicalize_habilitation(hb.get("type_habilitation", "")))
            # Certains libellés canoniques commencent déjà par « Habilitation ».
            # On évite donc un rendu du type « Habilitation Habilitation gestes et postures ».
            typ_label = typ if typ.lower().startswith("habilitation ") else f"Habilitation {typ}"
            if exp < today:
                critical.append(f"🔴 <b>{typ_label}</b> de <b>{nom}</b> expirée depuis le {date_fr(exp, 'court')}")
            elif exp <= in_60d:
                days = (exp - today).days
                day_word = "jour" if days == 1 else "jours"
                warning.append(f"🟠 <b>{typ_label}</b> de <b>{nom}</b> expire dans <b>{days} {day_word}</b> ({date_fr(exp, 'court')})")

    # Interventions : fenêtre opérationnelle et anomalies de données.
    iv_upcoming = pd.DataFrame()
    if not df_iv.empty and "date_intervention" in df_iv.columns:
        work = df_iv.copy()
        work["_date"] = pd.to_datetime(work["date_intervention"], errors="coerce").dt.date
        active_mask = ~work.get("statut", pd.Series(index=work.index, dtype=str)).astype(str).isin(["Annulé"])
        iv_upcoming = work[
            active_mask & work["_date"].notna() & (work["_date"] >= today) & (work["_date"] <= in_7d)
        ].copy()
        counters["interventions_7j"] = len(iv_upcoming)
        counters["urgences"] = int((work.get("statut", pd.Series(index=work.index, dtype=str)).astype(str) == "Urgence à pourvoir").sum())

        for _, row in work.iterrows():
            statut = str(row.get("statut", "") or "")
            date_val = row.get("_date")
            if pd.isna(date_val):
                info.append(f"🔵 Intervention avec une date invalide (ID {h(str(row.get('id', '')))}).")
                continue
            bname = h(benef_by_id.get(str(row.get("beneficiaire_id", "")), "Bénéficiaire inconnu"))
            start, end = row.get("heure_debut"), row.get("heure_fin")
            sm, em = _time_minutes(start), _time_minutes(end)
            if sm is None or em is None or em <= sm:
                critical.append(f"🔴 Horaire incohérent pour l'intervention de <b>{bname}</b> le {date_fr(date_val, 'court')} ({h(str(start))}–{h(str(end))}).")

            # Toute urgence non couverte, y compris passée, doit être visible.
            if statut == "Urgence à pourvoir":
                critical.append(
                    f"🚨 Intervention <b>non couverte</b> : {date_fr(date_val, 'court')} "
                    f"{h(str(start))}–{h(str(end))} ({h(str(row.get('type_intervention', '')) )}) — {bname}"
                )

            # Contrôles des affectations à venir : disponibilité + habilitations requises.
            iid = str(row.get("intervenant_id", "") or "")
            if iid and date_val >= today and statut not in ("Annulé", "Urgence à pourvoir"):
                person = interv_by_id.get(iid)
                if person and person.get("dispo") == "Indisponible":
                    critical.append(
                        f"🔴 <b>{h(person['nom'])}</b> est indiqué indisponible mais planifié le {date_fr(date_val, 'court')} "
                        f"{h(str(start))}–{h(str(end))} chez {bname}."
                    )
                required = required_habilitations(row.to_dict())
                if required:
                    cand_habs = latest_habs[
                        latest_habs["intervenant_id"].astype(str) == iid
                    ] if not latest_habs.empty and "intervenant_id" in latest_habs.columns else pd.DataFrame()
                    # Réutilise la même règle métier que le moteur de remplacement.
                    from .replacement_service import evaluate_habilitations
                    blockers, warn_h = evaluate_habilitations(cand_habs, required)
                    for blocker in blockers:
                        critical.append(
                            f"🔴 Mission du {date_fr(date_val, 'court')} chez <b>{bname}</b> : "
                            f"<b>{h(person['nom'] if person else 'Intervenant inconnu')}</b> — {h(blocker)}"
                        )
                    for w in warn_h:
                        warning.append(
                            f"🟠 Mission du {date_fr(date_val, 'court')} chez <b>{bname}</b> : "
                            f"<b>{h(person['nom'] if person else 'Intervenant inconnu')}</b> — {h(w)}"
                        )

        # Conflits de planning affectés dans les 7 jours.
        if not iv_upcoming.empty and "intervenant_id" in iv_upcoming.columns:
            assigned = iv_upcoming[
                iv_upcoming["intervenant_id"].notna() &
                ~iv_upcoming.get("statut", pd.Series(index=iv_upcoming.index, dtype=str)).astype(str).isin(["Annulé", "Urgence à pourvoir"])
            ].copy()
            for (iid, day), grp in assigned.groupby([assigned["intervenant_id"].astype(str), "_date"]):
                rows = list(grp.to_dict("records"))
                for a in range(len(rows)):
                    for b in range(a + 1, len(rows)):
                        sa, ea = _time_minutes(rows[a].get("heure_debut")), _time_minutes(rows[a].get("heure_fin"))
                        sb_, eb = _time_minutes(rows[b].get("heure_debut")), _time_minutes(rows[b].get("heure_fin"))
                        if None in (sa, ea, sb_, eb):
                            continue
                        if sa < eb and sb_ < ea:
                            nom = h(interv_by_id.get(str(iid), {}).get("nom", "Intervenant inconnu"))
                            critical.append(
                                f"🔴 Conflit de planning pour <b>{nom}</b> le {date_fr(day, 'court')} : "
                                f"{h(str(rows[a].get('heure_debut')))}–{h(str(rows[a].get('heure_fin')))} et "
                                f"{h(str(rows[b].get('heure_debut')))}–{h(str(rows[b].get('heure_fin')))}."
                            )

    # Fiches bénéficiaires sans référent et champs essentiels incomplets.
    if not df_benef.empty:
        active = df_benef[df_benef.get("statut", pd.Series(index=df_benef.index, dtype=str)).astype(str) == "Actif"] if "statut" in df_benef.columns else df_benef
        for _, row in active.iterrows():
            name = h(f"{row.get('prenom', '')} {row.get('nom', '')}".strip())
            if "intervenant_attitré_id" in active.columns and _dashboard_missing(row.get("intervenant_attitré_id")):
                warning.append(f"🟠 Bénéficiaire <b>{name}</b> sans intervenant référent défini.")
            missing = []
            for col, label in (("adresse", "adresse"), ("telephone", "téléphone"), ("besoins_recurrents", "besoins récurrents")):
                if col in active.columns and _dashboard_missing(row.get(col)):
                    missing.append(label)
            if missing:
                info.append(f"🔵 Fiche bénéficiaire <b>{name}</b> à compléter : {h(', '.join(missing))}.")

    # Fiches intervenants incomplètes. Pas de blocage : point d'attention seulement.
    if not df_interv.empty:
        for _, row in df_interv.iterrows():
            name = h(f"{row.get('prenom', '')} {row.get('nom', '')}".strip())
            missing = []
            for col, label in (("telephone", "téléphone"), ("email", "email"), ("zone_geo", "zone"), ("competences", "compétences"), ("disponibilites", "disponibilités")):
                if col in df_interv.columns and _dashboard_missing(row.get(col)):
                    missing.append(label)
            if missing:
                info.append(f"🔵 Fiche intervenant <b>{name}</b> à compléter : {h(', '.join(missing))}.")

    # Déduplication stricte des messages pour éviter le bruit visuel.
    def unique(items):
        seen, out = set(), []
        for item in items:
            key = _dashboard_plain(item)
            if key not in seen:
                seen.add(key)
                out.append(item)
        return out

    groups = {"critical": unique(critical), "warning": unique(warning), "info": unique(info)}
    return groups, counters, {
        "beneficiaires": df_benef,
        "intervenants": df_interv,
        "interventions": df_iv,
        "interventions_7j": iv_upcoming,
    }

def _dedupe_intervenants_for_planning(df):
    """Regroupe les fiches intervenant manifestement dupliquées pour l'affichage planning.

    On privilégie l'email, puis le téléphone, puis nom+prénom+zone. Chaque ligne
    canonique conserve `_intervenant_ids`, la liste des IDs regroupés, afin que les
    interventions déjà liées à une ancienne fiche restent visibles sur la même ligne.
    """
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.copy()

    work = df.copy()
    groups = {}
    order = []
    for idx, row in work.iterrows():
        email = _normalize_identity_text(row.get("email"))
        phone = "".join(ch for ch in str(row.get("telephone") or "") if ch.isdigit())
        nom = _normalize_identity_text(row.get("nom"))
        prenom = _normalize_identity_text(row.get("prenom"))
        zone = _normalize_identity_text(row.get("zone_geo"))
        if email:
            key = ("email", email)
        elif phone:
            key = ("phone", phone)
        else:
            key = ("name_zone", prenom, nom, zone)

        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((idx, str(row.get("id"))))

    rows = []
    for key in order:
        members = groups[key]
        # La ligne la plus récente dans le DataFrame devient la fiche canonique.
        canonical_idx = members[-1][0]
        row = work.loc[canonical_idx].copy()
        row["_intervenant_ids"] = [member_id for _, member_id in members]
        row["_duplicate_count"] = len(members)
        rows.append(row)

    return pd.DataFrame(rows).reset_index(drop=True)

def render():
    SID = st.session_state["structure_id"]
    USER_ID = st.session_state["user_id"]
    IS_ADMIN = st.session_state.get("is_admin", False)


    # ============================================================
    #  SIDEBAR
    # ============================================================
    st.sidebar.markdown("### ⚙️ Mon Compte")
    st.sidebar.caption(f"Connecté : {st.session_state.get('user_email', '')}")
    st.sidebar.caption(f"🏢 {st.session_state.get('structure_nom', 'Non assignée')}")

    peut_ia, nb_req, quota_max = peut_utiliser_ia()
    if quota_max >= 999999:
        st.sidebar.success("👑 Compte PRO illimité")
    else:
        st.sidebar.info(f"Requêtes IA : {nb_req} / {quota_max}")

    if IA_DISPONIBLE:
        st.sidebar.success("🔑 Gemini configuré")
    else:
        st.sidebar.warning("⚠️ Clé Gemini manquante")

    if st.sidebar.button("🚪 Se déconnecter"):
        audit("LOGOUT", "profils", USER_ID)
        sb.auth.sign_out()
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

    st.sidebar.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)

    # --- Admin sidebar ---
    # Les outils admin chargent plusieurs tables. Ils restent repliés par défaut
    # pour ne pas ralentir chaque changement de page.
    if IS_ADMIN:
        st.sidebar.markdown("### 👑 Administration")
        show_quick_admin = st.sidebar.checkbox(
            "Afficher les outils admin rapides", value=False, key="show_quick_admin"
        )

        if show_quick_admin:
            with st.sidebar.expander("➕ Créer un accès"):
                df_structs = sb_select("structures", order="nom")
                with st.form("form_add_user"):
                    struct_existante = st.selectbox(
                        "Structure existante",
                        [""] + (df_structs["nom"].tolist() if not df_structs.empty else [])
                    )
                    struct_nouvelle = st.text_input("OU nouvelle structure")
                    p_email = st.text_input("Email utilisateur")
                    p_pwd = st.text_input("Mot de passe temporaire")
                    p_duree = st.number_input("Durée d'accès (jours)", min_value=1, value=30)
                    btn_add = st.form_submit_button("Créer l'accès")

                    if btn_add and p_email and p_pwd:
                        if len(p_pwd) < 8:
                            st.error("8 caractères minimum pour le mot de passe.")
                        else:
                            nom_struct = struct_nouvelle.strip() or struct_existante
                            if not nom_struct:
                                st.error("Choisissez ou créez une structure.")
                            else:
                                struct_row = sb_select("structures", {"nom": nom_struct})
                                if struct_row.empty:
                                    struct_row = sb_insert("structures", {"nom": nom_struct})
                                    struct_id = struct_row["id"] if struct_row else None
                                else:
                                    struct_id = struct_row.iloc[0]["id"]

                                if struct_id:
                                    date_fin = (datetime.date.today() + datetime.timedelta(days=int(p_duree))).isoformat()
                                    try:
                                        auth_res = create_auth_user(p_email, p_pwd)
                                        new_uid = auth_res.user.id
                                        sb_insert("profils", {
                                            "id": new_uid,
                                            "structure_id": struct_id,
                                            "email": p_email,
                                            "est_admin": False,
                                            "statut_abonnement": "ESSAI",
                                            "quota_max_ia": 20,
                                            "date_fin_essai": date_fin
                                        })
                                        audit("CREATE_USER", "profils", new_uid, {"structure": nom_struct})
                                        st.success(f"✅ Accès créé pour {p_email} jusqu'au {date_fr(date_fin, 'court')}")
                                    except Exception:
                                        logger.exception("Création d'accès utilisateur impossible")
                                        st.error("Impossible de créer l'accès. Consultez les journaux administrateur si le problème persiste.")

            with st.sidebar.expander("📊 Quotas IA"):
                df_users = sb_select("profils", order="email")
                if not df_users.empty:
                    st.dataframe(
                        df_users[["email", "nb_requetes_ia", "quota_max_ia", "statut_abonnement", "date_fin_essai"]],
                        use_container_width=True, hide_index=True
                    )
                    email_reset = st.text_input("Email à réinitialiser")
                    if st.button("Remettre à 0") and email_reset:
                        sb_update("profils", {"nb_requetes_ia": 0}, "email", email_reset)
                        st.success("Quota réinitialisé.")

    st.sidebar.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)


    # ============================================================
    #  MENU PRINCIPAL — Admin voit tout + Administration
    # ============================================================
    st.sidebar.markdown("### 📋 Menu")

    _onglets = [
        "🏠 Tableau de bord",
        "🧑‍🤝‍🧑 Vivier & Sourcing",
        "🎯 Matching IA",
        "❤️ Bénéficiaires",
        "📝 Documents & Transmissions",
        "📅 Plannings & Urgences",
        "✅ Conformité & Habilitations",
        "📊 Suivi des heures",
        "👤 Mon Profil",
    ]
    if IS_ADMIN:
        _onglets.insert(0, "🛠️ Administration")

    onglet = st.sidebar.radio("Navigation", _onglets, label_visibility="collapsed")

    st.markdown(f"# {onglet}")
    st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)


    # ============================================================
    #  🏠 TABLEAU DE BORD — CENTRE DE PILOTAGE
    # ============================================================
    if onglet == "🏠 Tableau de bord":
        checked_at = datetime.datetime.now()
        groups, counters, dashboard_data = _dashboard_collect_checks(SID)
        st.session_state["dashboard_last_check"] = checked_at

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("👥 Bénéficiaires actifs", counters["beneficiaires_actifs"])
        col2.metric("🧑‍⚕️ Intervenants dispo", counters["intervenants_dispo"])
        col3.metric("📅 Interventions (7j)", counters["interventions_7j"])
        col4.metric("🚨 Urgences", counters["urgences"])

        total_alerts = sum(len(v) for v in groups.values())
        c_refresh, c_ai, c_time = st.columns([1.1, 1.4, 2.5])
        with c_refresh:
            if st.button("🔄 Actualiser les contrôles", use_container_width=True):
                st.session_state.pop("dashboard_ai_summary", None)
                st.session_state.pop("dashboard_ai_fingerprint", None)
                st.rerun()
        with c_time:
            st.caption(f"Dernier contrôle automatique : {checked_at.strftime('%H:%M:%S')} · {total_alerts} point(s) détecté(s)")

        fingerprint = _dashboard_alert_fingerprint(groups)
        if st.session_state.get("dashboard_ai_fingerprint") != fingerprint:
            # Une synthèse précédente ne doit jamais rester affichée après une évolution des alertes.
            st.session_state.pop("dashboard_ai_summary", None)
            st.session_state["dashboard_ai_fingerprint"] = fingerprint

        with c_ai:
            analyse_click = st.button(
                "🧠 Analyser la situation avec l'IA",
                use_container_width=True,
                disabled=(total_alerts == 0 or not IA_DISPONIBLE),
            )

        if analyse_click:
            lines = []
            labels = {"critical": "CRITIQUE", "warning": "À TRAITER", "info": "INFORMATION"}
            for level in ("critical", "warning", "info"):
                for item in groups[level]:
                    lines.append(f"[{labels[level]}] {_dashboard_plain(item)}")
            prompt = f"""
Tu es l'assistant de pilotage d'une agence d'aide à domicile utilisant OmniCoord.
Tu dois uniquement synthétiser et hiérarchiser les anomalies déterministes ci-dessous.
N'invente aucun problème, aucune date, aucun nom et aucune donnée absente.
Ne décide jamais de la conformité : les contrôles OmniCoord font foi.
Donne une synthèse courte et actionnable en français :
- une phrase de situation générale ;
- 3 à 5 priorités maximum, classées par urgence ;
- si utile, une dernière ligne 'À anticiper'.
Pas de tableau, pas de jargon, pas de diagnostic médical.

Contrôles OmniCoord :
{chr(10).join(lines)}
""".strip()
            with st.spinner("Analyse des priorités en cours…"):
                summary = appel_ia_texte(prompt)
            if summary:
                st.session_state["dashboard_ai_summary"] = summary
                st.session_state["dashboard_ai_fingerprint"] = fingerprint

        if st.session_state.get("dashboard_ai_summary"):
            st.markdown("### 🧠 Synthèse intelligente")
            st.info(st.session_state["dashboard_ai_summary"])
            st.caption("L'IA explique et priorise les alertes détectées par OmniCoord ; elle ne modifie aucune règle métier.")

        st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
        st.subheader("🔔 Alertes & points d'attention")

        if total_alerts == 0:
            st.markdown('<div class="oc-card oc-card-ok"><b>✅ Aucun point bloquant ou à surveiller détecté.</b></div>', unsafe_allow_html=True)
        else:
            if groups["critical"]:
                with st.expander(f"🔴 Priorité critique ({len(groups['critical'])})", expanded=True):
                    for a in groups["critical"]:
                        st.markdown(f'<div class="alert-box alert-box-rouge">{a}</div>', unsafe_allow_html=True)
            if groups["warning"]:
                with st.expander(f"🟠 À traiter prochainement ({len(groups['warning'])})", expanded=True):
                    for a in groups["warning"]:
                        st.markdown(f'<div class="alert-box alert-box-orange">{a}</div>', unsafe_allow_html=True)
            if groups["info"]:
                with st.expander(f"🔵 Informations / fiches à compléter ({len(groups['info'])})"):
                    for a in groups["info"]:
                        st.markdown(f'<div class="alert-box alert-box-bleu">{a}</div>', unsafe_allow_html=True)

        # Interventions du jour
        st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
        today = datetime.date.today()
        st.subheader(f"📅 Interventions du jour — {date_fr(today, 'long')}")
        df_iv = dashboard_data["interventions"]
        if df_iv.empty or "date_intervention" not in df_iv.columns:
            st.caption("Aucune intervention planifiée aujourd'hui.")
        else:
            df_jour = df_iv[pd.to_datetime(df_iv["date_intervention"], errors="coerce").dt.date == today].copy()
            if df_jour.empty:
                st.caption("Aucune intervention planifiée aujourd'hui.")
            else:
                df_benef_noms = dashboard_data["beneficiaires"]
                df_interv_noms = dashboard_data["intervenants"]
                benef_noms = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_benef_noms.iterrows()} if not df_benef_noms.empty else {}
                interv_noms = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_interv_noms.iterrows()} if not df_interv_noms.empty else {}
                for _, row in df_jour.sort_values("heure_debut").iterrows():
                    coul = {"Planifié":"#4c8dfa", "Urgence à pourvoir":"#e0554f", "Réalisé":"#3fae74", "Annulé":"#8996a3"}.get(row.get("statut"), "#8996a3")
                    b_nom = h(benef_noms.get(str(row.get("beneficiaire_id", "")), "—"))
                    i_nom = h(interv_noms.get(str(row.get("intervenant_id", "")), "Non assigné"))
                    st.markdown(f"""
                        <div class="oc-card" style="border-left-color:{coul}; padding:12px 16px;">
                            <b>{h(str(row.get('heure_debut', '')))} – {h(str(row.get('heure_fin', '')))}</b> · {h(str(row.get('type_intervention', '')))}
                            <span class="oc-badge" style="background:{coul}; float:right;">{h(str(row.get('statut', '')))}</span><br>
                            <span style="color:#b8c2cc;">👤 {b_nom} &nbsp;•&nbsp; 🧑‍⚕️ {i_nom}</span>
                        </div>
                    """, unsafe_allow_html=True)

    # ============================================================
    #  🧑‍🤝‍🧑 VIVIER & SOURCING
    # ============================================================
    elif onglet == "🧑‍🤝‍🧑 Vivier & Sourcing":
        tab_liste, tab_ajout, tab_sourcing = st.tabs(["📋 Vivier", "➕ Ajouter", "🔎 Sourcing externe"])

        with tab_liste:
            df_interv = sb_select("intervenants", {"structure_id": SID}, order="date_ajout")
            if not df_interv.empty and "deleted_at" in df_interv.columns:
                df_interv = df_interv[df_interv["deleted_at"].isna()]
            # Chargement unique des habilitations : évite une requête Supabase par fiche.
            df_habs_vivier = sb_select("habilitations", {"structure_id": SID})
            col_f1, col_f2 = st.columns(2)
            filtre_type = col_f1.selectbox("Statut", ["Tous", "Interne", "Vivier candidat", "Externe ponctuel"])
            filtre_dispo = col_f2.selectbox("Disponibilité", ["Toutes", "Disponible", "En mission", "Indisponible"])

            df_aff = df_interv.copy() if not df_interv.empty else pd.DataFrame()
            if not df_aff.empty:
                if filtre_type != "Tous": df_aff = df_aff[df_aff["type_statut"] == filtre_type]
                if filtre_dispo != "Toutes": df_aff = df_aff[df_aff["statut_dispo"] == filtre_dispo]

            col_m1, col_m2, col_m3 = st.columns(3)
            col_m1.metric("Total", len(df_interv))
            col_m2.metric("Disponibles", len(df_interv[df_interv["statut_dispo"] == "Disponible"]) if not df_interv.empty else 0)
            col_m3.metric("Vivier candidats", len(df_interv[df_interv["type_statut"] == "Vivier candidat"]) if not df_interv.empty else 0)

            if df_aff.empty:
                st.info("Aucun intervenant pour ce filtre.")
            else:
                for _, row in df_aff.iterrows():
                    coul = {"Disponible":"#3fae74","En mission":"#d99a3d","Indisponible":"#e0554f"}.get(row["statut_dispo"], "#8996a3")
                    st.markdown(f"""
                        <div class="oc-card" style="border-left-color:{coul};">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <span style="font-size:17px; font-weight:700;">{h(row['prenom'])} {h(row['nom'])}</span>
                                <span class="oc-badge" style="background:{coul};">{h(row['statut_dispo'])}</span>
                            </div>
                            <div style="color:#b8c2cc; font-size:13px; margin-top:4px;">
                                {h(row['type_statut'])} • {h(row['zone_geo']) or 'Zone non précisée'} • Source : {h(row['source']) or 'N/C'}
                            </div>
                            <div style="color:#e6ecf2; margin-top:8px;"><b>Compétences :</b> {h(row['competences']) or 'Non renseigné'}</div>
                        </div>
                    """, unsafe_allow_html=True)

                    with st.expander(f"Détails / actions — {row['prenom']} {row['nom']}"):
                        tab_profil_i, tab_habs_i = st.tabs(["✏️ Modifier la fiche", "🎓 Habilitations & conformité"])

                        with tab_profil_i:
                            parcours_actuel, soft_actuel = _split_experience_softskills(row.get("experience_texte"))
                            with st.form(f"edit_intervenant_{row['id']}"):
                                e1, e2 = st.columns(2)
                                with e1:
                                    e_nom = st.text_input("Nom *", value=str(row.get("nom") or ""), key=f"enom_{row['id']}")
                                    e_prenom = st.text_input("Prénom *", value=str(row.get("prenom") or ""), key=f"eprenom_{row['id']}")
                                    e_tel = st.text_input("Téléphone", value=str(row.get("telephone") or ""), key=f"etel_{row['id']}")
                                    e_email = st.text_input("Email", value=str(row.get("email") or ""), key=f"eemail_{row['id']}")
                                    statuts_i = ["Interne", "Vivier candidat", "Externe ponctuel"]
                                    e_type = st.selectbox("Statut", statuts_i, index=statuts_i.index(row.get("type_statut")) if row.get("type_statut") in statuts_i else 0, key=f"etype_{row['id']}")
                                    dispos_i = ["Disponible", "En mission", "Indisponible"]
                                    e_dispo = st.selectbox("Disponibilité", dispos_i, index=dispos_i.index(row.get("statut_dispo")) if row.get("statut_dispo") in dispos_i else 0, key=f"edispo_{row['id']}")
                                with e2:
                                    e_comp = st.text_area("Compétences / gestes techniques", value=str(row.get("competences") or ""), key=f"ecomp_{row['id']}")
                                    e_parcours = st.text_area("Parcours professionnel", value=parcours_actuel, key=f"eparcours_{row['id']}")
                                    e_zone = st.text_input("Zone géographique", value=str(row.get("zone_geo") or ""), key=f"ezone_{row['id']}")
                                    e_disponibilites = st.text_input("Disponibilités détaillées", value=str(row.get("disponibilites") or ""), key=f"edetailsdispo_{row['id']}")
                                    sources_i = ["Vivier interne","CVthèque","Annonce","Réseau / cooptation","Candidature spontanée"]
                                    source_actuelle = row.get("source") if row.get("source") in sources_i else "Vivier interne"
                                    e_source = st.selectbox("Source", sources_i, index=sources_i.index(source_actuelle), key=f"esource_{row['id']}")
                                e_soft = st.text_area("Observations personnalité / soft skills", value=soft_actuel, key=f"esoft_{row['id']}")
                                save_i = st.form_submit_button("💾 Enregistrer les modifications")

                            if save_i:
                                if not e_nom.strip() or not e_prenom.strip():
                                    st.error("Le nom et le prénom sont obligatoires.")
                                else:
                                    payload_i = {
                                        "nom": e_nom.strip(), "prenom": e_prenom.strip(),
                                        "telephone": e_tel.strip(), "email": e_email.strip(),
                                        "type_statut": e_type, "statut_dispo": e_dispo,
                                        "competences": e_comp.strip(),
                                        "experience_texte": _experience_with_softskills(e_parcours, e_soft),
                                        "zone_geo": e_zone.strip(), "disponibilites": e_disponibilites.strip(),
                                        "source": e_source,
                                    }
                                    if sb_update("intervenants", payload_i, "id", row["id"]):
                                        audit("UPDATE_INTERVENANT", "intervenants", str(row["id"]), {"champs": list(payload_i.keys())})
                                        st.success("Fiche intervenant mise à jour.")
                                        st.rerun()

                            st.markdown("---")
                            confirm_archive = st.checkbox(
                                "Confirmer le retrait du vivier", key=f"confirm_del_{row['id']}",
                                help="La fiche est archivée pour préserver l'historique des interventions ; elle disparaît des listes actives.",
                            )
                            if st.button("🗑️ Archiver / retirer", key=f"del_{row['id']}", disabled=not confirm_archive):
                                if sb_update("intervenants", {"deleted_at": datetime.datetime.utcnow().isoformat(), "statut_dispo": "Indisponible"}, "id", row["id"]):
                                    audit("ARCHIVE_INTERVENANT", "intervenants", str(row["id"]))
                                    st.success("Intervenant archivé et retiré du vivier actif.")
                                    st.rerun()

                        with tab_habs_i:
                            hab_types = CANONICAL_HABILITATIONS
                            hab_i = pd.DataFrame()
                            if not df_habs_vivier.empty and "intervenant_id" in df_habs_vivier.columns:
                                hab_i = df_habs_vivier[df_habs_vivier["intervenant_id"].astype(str) == str(row["id"])].copy()

                            if hab_i.empty:
                                st.info("Aucune habilitation enregistrée pour cet intervenant. Vous pouvez en ajouter une maintenant ou plus tard.")
                            else:
                                st.caption("Historique des habilitations. Un renouvellement crée une nouvelle ligne afin de conserver la trace de l'ancienne.")
                                hab_i["_date_exp"] = pd.to_datetime(hab_i["date_expiration"], errors="coerce").dt.date
                                hab_i = hab_i.sort_values(["type_habilitation", "date_obtention"], ascending=[True, False])
                                for _, hb in hab_i.iterrows():
                                    d_exp = hb.get("_date_exp")
                                    if pd.isna(d_exp) or d_exp == NO_EXPIRY_DATE:
                                        statut_h = "🟢 Valide sans expiration"
                                    elif d_exp < datetime.date.today():
                                        statut_h = f"🔴 Expirée le {d_exp.strftime('%d/%m/%Y')}"
                                    elif d_exp <= datetime.date.today() + datetime.timedelta(days=60):
                                        statut_h = f"🟠 Valide jusqu'au {d_exp.strftime('%d/%m/%Y')}"
                                    else:
                                        statut_h = f"🟢 Valide jusqu'au {d_exp.strftime('%d/%m/%Y')}"
                                    with st.expander(f"{canonicalize_habilitation(hb.get('type_habilitation','Habilitation'))} — {statut_h}"):
                                        st.write(f"Date d'obtention : {hb.get('date_obtention') or 'Non renseignée'}")
                                        correction = st.checkbox("Corriger cette saisie", key=f"corr_h_{hb['id']}")
                                        if correction:
                                            with st.form(f"corr_hab_{hb['id']}"):
                                                tcur = canonicalize_habilitation(hb.get("type_habilitation"))
                                                tcur = tcur if tcur in hab_types else "Autre"
                                                c_type = st.selectbox("Type", hab_types, index=hab_types.index(tcur), key=f"ct_{hb['id']}")
                                                obt0 = pd.to_datetime(hb.get("date_obtention"), errors="coerce")
                                                obt0 = obt0.date() if pd.notna(obt0) else datetime.date.today()
                                                c_obt = st.date_input("Date d'obtention", value=obt0, key=f"co_{hb['id']}")
                                                sans_exp0 = pd.isna(d_exp) or d_exp == NO_EXPIRY_DATE
                                                c_sans = st.checkbox("Valide sans date d'expiration", value=sans_exp0, key=f"cs_{hb['id']}")
                                                exp0 = (datetime.date.today() + datetime.timedelta(days=365)) if sans_exp0 or pd.isna(d_exp) else d_exp
                                                c_exp = None if c_sans else st.date_input("Date d'expiration", value=exp0, key=f"ce_{hb['id']}")
                                                if st.form_submit_button("💾 Corriger"):
                                                    if c_exp is not None and c_exp <= c_obt:
                                                        st.error("La date d'expiration doit être après la date d'obtention.")
                                                    else:
                                                        p_h = {"type_habilitation": canonicalize_habilitation(c_type), "date_obtention": c_obt.isoformat(), "date_expiration": NO_EXPIRY_DATE.isoformat() if c_sans else c_exp.isoformat()}
                                                        if sb_update("habilitations", p_h, "id", hb["id"]):
                                                            audit("UPDATE_HABILITATION", "habilitations", str(hb["id"]))
                                                            st.success("Saisie corrigée.")
                                                            st.rerun()

                                        st.markdown("---")
                                        confirm_remove_h = st.checkbox(
                                            "Confirmer le retrait de cette habilitation",
                                            key=f"confirm_remove_h_{hb['id']}",
                                            help=(
                                                "À utiliser pour corriger une saisie erronée ou un doublon. "
                                                "Un renouvellement normal doit rester dans l'historique."
                                            ),
                                        )
                                        if st.button(
                                            "🗑️ Retirer cette habilitation",
                                            key=f"remove_h_{hb['id']}",
                                            disabled=not confirm_remove_h,
                                            type="secondary",
                                        ):
                                            if sb_delete("habilitations", "id", str(hb["id"])):
                                                audit(
                                                    "DELETE_HABILITATION",
                                                    "habilitations",
                                                    str(hb["id"]),
                                                    {
                                                        "intervenant_id": str(row["id"]),
                                                        "type_canonique": canonicalize_habilitation(hb.get("type_habilitation")),
                                                        "motif": "correction_saisie",
                                                    },
                                                )
                                                st.success("Habilitation retirée de la fiche.")
                                                st.rerun()

                            st.markdown("#### ➕ Ajouter / renouveler une habilitation")
                            mode_key = f"new_hab_mode_{row['id']}"
                            mode_h = st.radio("Validité", ["Avec date d'expiration", "Valide sans date d'expiration"], horizontal=True, key=mode_key)
                            with st.form(f"add_hab_interv_{row['id']}", clear_on_submit=True):
                                n_type = st.selectbox("Type d'habilitation", hab_types, key=f"nt_{row['id']}")
                                n_obt = st.date_input("Date d'obtention", value=datetime.date.today(), key=f"no_{row['id']}")
                                n_exp = None
                                if mode_h == "Avec date d'expiration":
                                    n_exp = st.date_input("Date d'expiration", value=datetime.date.today() + datetime.timedelta(days=365), key=f"ne_{row['id']}")
                                add_h = st.form_submit_button("➕ Ajouter à la fiche")
                            if add_h:
                                if n_exp is not None and n_exp <= n_obt:
                                    st.error("La date d'expiration doit être après la date d'obtention.")
                                else:
                                    existing_records = hab_i.to_dict("records") if not hab_i.empty else []
                                    duplicate_reason = duplicate_habilitation_reason(
                                        existing_records, n_type, n_obt, permanent=n_exp is None
                                    )
                                    if duplicate_reason:
                                        st.warning(duplicate_reason)
                                    else:
                                        new_h = sb_insert("habilitations", {
                                            "structure_id": SID, "intervenant_id": str(row["id"]),
                                            "type_habilitation": canonicalize_habilitation(n_type), "date_obtention": n_obt.isoformat(),
                                            "date_expiration": n_exp.isoformat() if n_exp is not None else NO_EXPIRY_DATE.isoformat(),
                                        })
                                        if new_h:
                                            audit("CREATE_HABILITATION", "habilitations", new_h.get("id"), {"intervenant_id": str(row["id"]), "type_canonique": canonicalize_habilitation(n_type)})
                                            st.success("Habilitation ajoutée à la fiche. Le moteur de matching la prendra en compte automatiquement.")
                                            st.rerun()

        with tab_ajout:
            with st.form("form_ajout_interv", clear_on_submit=True):
                col1, col2 = st.columns(2)
                with col1:
                    nom = st.text_input("Nom *")
                    prenom = st.text_input("Prénom *")
                    telephone = st.text_input("Téléphone")
                    email_i = st.text_input("Email")
                    type_statut = st.selectbox("Statut", ["Interne", "Vivier candidat", "Externe ponctuel"])
                with col2:
                    competences = st.text_area("Compétences / gestes techniques")
                    experience_texte = st.text_area("Parcours professionnel")
                    zone_geo = st.text_input("Zone géographique")
                    disponibilites = st.text_input("Disponibilités")
                    source = st.selectbox("Source", ["Vivier interne","CVthèque","Annonce","Réseau / cooptation","Candidature spontanée"])

                # Champ soft skills / personnalité — utilisé par le matching IA
                st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
                st.markdown("**🧠 Personnalité & savoir-être** *(observations du coordinateur — enrichit l'analyse IA)*")
                soft_skills = st.text_area(
                    "Observations personnalité / soft skills",
                    placeholder="Ex : très à l'aise avec les personnes atteintes d'Alzheimer, grande patience, "
                                "bénévolat auprès de personnes âgées, calme en situation de stress, "
                                "très empathique, bonne communication avec les familles...",
                    height=100,
                    help="Ces observations sont analysées par l'IA lors du matching pour évaluer l'adéquation humaine avec le bénéficiaire."
                )

                if st.form_submit_button("Ajouter au vivier") and nom and prenom:
                    duplicate = False
                    if not df_interv.empty:
                        same_email = pd.Series(False, index=df_interv.index)
                        if email_i.strip() and "email" in df_interv.columns:
                            same_email = df_interv["email"].fillna("").astype(str).str.strip().str.casefold() == email_i.strip().casefold()
                        same_identity = (
                            df_interv["nom"].fillna("").astype(str).str.strip().str.casefold().eq(nom.strip().casefold())
                            & df_interv["prenom"].fillna("").astype(str).str.strip().str.casefold().eq(prenom.strip().casefold())
                        )
                        if telephone.strip() and "telephone" in df_interv.columns:
                            same_identity &= df_interv["telephone"].fillna("").astype(str).str.replace(r"\D", "", regex=True).eq("".join(ch for ch in telephone if ch.isdigit()))
                        duplicate = bool((same_email | same_identity).any())
                    if duplicate:
                        st.error("Une fiche similaire existe déjà dans le vivier. Vérifiez-la avant de créer un doublon.")
                    else:
                        new_row = sb_insert("intervenants", {
                        "structure_id": SID, "nom": nom.strip(), "prenom": prenom.strip(),
                        "telephone": telephone, "email": email_i, "type_statut": type_statut,
                        "competences": competences, "experience_texte": experience_texte,
                        "zone_geo": zone_geo, "disponibilites": disponibilites,
                        "statut_dispo": "Disponible", "source": source,
                        "date_ajout": datetime.date.today().isoformat(),
                        "disponibilites": disponibilites,
                        # Stocké dans experience_texte enrichi si pas de colonne dédiée
                        # On préfixe pour que l'IA puisse le distinguer
                        "experience_texte": f"{experience_texte}\n\n[SOFT SKILLS / PERSONNALITÉ] : {soft_skills}" if soft_skills else experience_texte,
                        })
                        if new_row:
                            audit("CREATE_INTERVENANT", "intervenants", new_row.get("id"))
                            st.success(f"{prenom} {nom} ajouté(e).")
                            st.rerun()

        with tab_sourcing:
            st.subheader("🔎 Sourcing & analyse de CV")
            st.caption("Le CV est analysé pour préremplir la fiche. Le coordinateur garde toujours la validation finale avant l'enregistrement.")

            uploaded_cv = st.file_uploader("Importer un CV", type=["pdf", "txt"], help="PDF texte ou TXT, 8 Mo maximum.")
            if st.button("🧠 Analyser le CV avec l'IA", disabled=uploaded_cv is None):
                try:
                    with st.spinner("Lecture et structuration du CV..."):
                        data_cv = analyse_cv(uploaded_cv)
                    if data_cv:
                        st.session_state["cv_analysis"] = data_cv
                        st.success("CV analysé. Vérifiez les informations avant l'ajout au vivier.")
                except ValidationError as exc:
                    st.error(str(exc))

            data_cv = st.session_state.get("cv_analysis")
            if data_cv:
                detected = [canonicalize_habilitation(v) for v in (data_cv.get("habilitations_detectees") or [])]
                detected = list(dict.fromkeys(v for v in detected if v))
                if detected:
                    st.info("Conformité détectée dans le CV : " + ", ".join(detected) + ". Vérifiez les justificatifs avant de l'enregistrer dans Conformité & Habilitations.")
                with st.form("form_cv_candidate", clear_on_submit=True):
                    cva, cvb = st.columns(2)
                    with cva:
                        cv_nom = st.text_input("Nom *", value=str(data_cv.get("nom") or ""))
                        cv_prenom = st.text_input("Prénom *", value=str(data_cv.get("prenom") or ""))
                        cv_tel = st.text_input("Téléphone", value=str(data_cv.get("telephone") or ""))
                        cv_email = st.text_input("Email", value=str(data_cv.get("email") or ""))
                        cv_statut = st.selectbox("Statut", ["Vivier candidat", "Interne", "Externe ponctuel"])
                    with cvb:
                        cv_comp = st.text_area("Compétences / gestes techniques", value=str(data_cv.get("competences") or ""))
                        cv_exp = st.text_area("Parcours professionnel", value=str(data_cv.get("experience_texte") or ""))
                        cv_zone = st.text_input("Zone géographique", value=str(data_cv.get("zone_geo") or ""))
                        cv_dispo = st.text_input("Disponibilités", value=str(data_cv.get("disponibilites") or ""))
                        cv_soft = st.text_area("Observations personnalité / soft skills", value=str(data_cv.get("soft_skills") or ""))
                    if st.form_submit_button("✅ Valider et ajouter au vivier"):
                        if not cv_nom.strip() or not cv_prenom.strip():
                            st.error("Nom et prénom sont obligatoires.")
                        else:
                            existing = sb_select("intervenants", {"structure_id": SID})
                            duplicate = False
                            if not existing.empty:
                                if "deleted_at" in existing.columns:
                                    existing = existing[existing["deleted_at"].isna()]
                                if cv_email.strip() and "email" in existing.columns:
                                    duplicate = bool((existing["email"].fillna("").astype(str).str.strip().str.casefold() == cv_email.strip().casefold()).any())
                                if not duplicate:
                                    duplicate = bool((
                                        existing["nom"].fillna("").astype(str).str.strip().str.casefold().eq(cv_nom.strip().casefold())
                                        & existing["prenom"].fillna("").astype(str).str.strip().str.casefold().eq(cv_prenom.strip().casefold())
                                    ).any())
                            if duplicate:
                                st.error("Un candidat similaire existe déjà. Aucune nouvelle fiche n'a été créée.")
                            else:
                                enriched_exp = cv_exp
                                if cv_soft.strip():
                                    enriched_exp = f"{cv_exp}\n\n[SOFT SKILLS / PERSONNALITÉ] : {cv_soft}".strip()
                                new_cv = sb_insert("intervenants", {
                                    "structure_id": SID, "nom": cv_nom.strip(), "prenom": cv_prenom.strip(),
                                    "telephone": cv_tel.strip(), "email": cv_email.strip(), "type_statut": cv_statut,
                                    "competences": cv_comp.strip(), "experience_texte": enriched_exp,
                                    "zone_geo": cv_zone.strip(), "disponibilites": cv_dispo.strip(),
                                    "statut_dispo": "Disponible", "source": "CVthèque",
                                    "date_ajout": datetime.date.today().isoformat(),
                                })
                                if new_cv:
                                    audit("CREATE_INTERVENANT_FROM_CV", "intervenants", new_cv.get("id"), {"habilitations_detectees": detected})
                                    st.session_state.pop("cv_analysis", None)
                                    st.success("Candidat ajouté au vivier. Les habilitations détectées restent à valider sur justificatif.")
                                    st.rerun()

            st.markdown("---")
            st.markdown("#### Recherche externe")
            col_s1, col_s2 = st.columns(2)
            metier = col_s1.text_input("Métier", value="Auxiliaire de vie")
            zone = col_s2.text_input("Zone géographique", value="")
            if st.button("Générer les liens"):
                q = urllib.parse.quote(f"{metier} {zone}".strip())
                st.markdown(f"""
                    <div class="oc-card"><b>🔗 LinkedIn</b><br><a href="https://www.linkedin.com/search/results/people/?keywords={q}" target="_blank">Rechercher sur LinkedIn</a></div>
                    <div class="oc-card"><b>🔗 CV publics (Google)</b><br><a href="https://www.google.com/search?q={q}+CV+filetype:pdf" target="_blank">Rechercher des CV PDF</a></div>
                    <div class="oc-card"><b>🔗 Groupes Facebook emploi</b><br><a href="https://www.facebook.com/search/groups/?q={q}+emploi" target="_blank">Groupes emploi local</a></div>
                    <div class="oc-card"><b>🔗 Indeed</b><br><a href="https://www.indeed.fr/jobs?q={q}" target="_blank">Voir sur Indeed</a></div>
                """, unsafe_allow_html=True)


    # ============================================================
    #  🎯 MATCHING IA
    # ============================================================
    elif onglet == "🎯 Matching IA":
        df_benef = sb_select("beneficiaires", {"structure_id": SID, "statut": "Actif"}, order="nom")
        df_interv_dispo = sb_select("intervenants", {"structure_id": SID}, order="nom")
        if not df_interv_dispo.empty:
            df_interv_dispo = df_interv_dispo[df_interv_dispo["statut_dispo"] != "Indisponible"]

        if df_benef.empty:
            st.info("Ajoutez d'abord un bénéficiaire.")
        elif df_interv_dispo.empty:
            st.info("Aucun intervenant disponible.")
        else:
            benef_labels = {f"{r['prenom']} {r['nom']}": r['id'] for _, r in df_benef.iterrows()}
            benef_choisi_label = st.selectbox("Bénéficiaire", list(benef_labels.keys()))
            benef_id = benef_labels[benef_choisi_label]
            benef_row = df_benef[df_benef["id"] == benef_id].iloc[0]

            st.markdown(f"""
                <div class="oc-card">
                    <b>Besoins récurrents :</b> {h(benef_row.get('besoins_recurrents', '') or 'Non renseigné')}<br>
                    <b>Gestes techniques :</b> {h(benef_row.get('gestes_techniques', '') or 'Non renseigné')}<br>
                    <b>Horaires :</b> {h(benef_row.get('besoins_horaires', '') or 'Non renseigné')}<br>
                    <b>Dépendance :</b> {h(benef_row.get('niveau_dependance', '') or 'Non renseigné')}
                </div>
            """, unsafe_allow_html=True)

            if st.button("🎯 Lancer le matching IA"):
                try:
                    # Un nouveau matching ne doit jamais réutiliser des états IA
                    # ou des résultats issus de l'exécution précédente.
                    st.session_state.pop("resultats_matching", None)
                    st.session_state.pop("benef_matching_label", None)
                    st.session_state.pop("_ai_error_shown", None)
                    with st.spinner("Préfiltrage, scoring puis analyse IA des meilleurs candidats..."):
                        resultats = match_beneficiary(
                            SID,
                            benef_row.to_dict(),
                            df_interv_dispo,
                            ai_top_k=5,
                        )
                    st.session_state["resultats_matching"] = resultats
                    st.session_state["benef_matching_label"] = benef_choisi_label
                    if len(df_interv_dispo) > 5:
                        st.caption(
                            f"⚡ {len(df_interv_dispo)} candidats préclassés en Python ; "
                            "l'IA peut analyser jusqu'aux 5 meilleurs pour limiter coût et latence."
                        )
                except DatabaseError:
                    st.error("Impossible de charger les données nécessaires au matching.")

            # ── Affichage des résultats ────────────────────────────────────────────
            matching_results = st.session_state.get("resultats_matching") or []
            same_beneficiary = st.session_state.get("benef_matching_label") == benef_choisi_label
            if matching_results and same_beneficiary:
                st.markdown("### 📊 Résultats du matching")

                ai_count = sum(1 for r in matching_results if r.get("ai_used"))
                if ai_count:
                    st.success(f"✨ Analyse IA effectuée sur {ai_count} profil(s). Le score final reste calculé par OmniCoord.")
                else:
                    st.info("🧮 Classement métier uniquement : aucune note IA n'est affichée ni simulée.")

                OBJECTIVE_DIMENSIONS = [
                    ("score_competences",  "🛠️ Compétences techniques", "#2f7cf6"),
                    ("score_habilitations","🎓 Habilitations",          "#7c3aed"),
                    ("score_compatibilite","📍 Compatibilité pratique",  "#3fae74"),
                ]
                AI_DIMENSIONS = [
                    ("score_empathie",     "❤️ Empathie & bienveillance","#e0554f"),
                    ("score_soft_skills",  "🧠 Soft skills",             "#d99a3d"),
                ]

                for rang, res in enumerate(matching_results, 1):
                    score = int(res.get("score_global", 0))
                    coul_score = "#3fae74" if score >= 70 else ("#d99a3d" if score >= 45 else "#e0554f")
                    nom_interv = h(res.get("intervenant_nom", ""))
                    statut_interv = h(res.get("intervenant_statut", ""))
                    rangs_emoji = {1: "🥇", 2: "🥈", 3: "🥉"}
                    rang_label = rangs_emoji.get(rang, f"#{rang}")

                    # Carte de résumé. Le statut IA est explicite afin de ne
                    # jamais présenter une donnée déterministe comme une analyse IA.
                    ai_used = bool(res.get("ai_used"))
                    analyse_label = "✨ Analyse IA effectuée" if ai_used else "🧮 Classement métier uniquement"
                    profil_resume = res.get("profil_humain", "") if ai_used else "Score fondé sur les données métier enregistrées."
                    st.markdown(f"""
                        <div class="oc-card" style="border-left-color:{coul_score};">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <div>
                                    <span style="font-size:18px; font-weight:800;">{rang_label} {nom_interv}</span>
                                    <span style="color:#8996a3; font-size:13px; margin-left:10px;">{statut_interv}</span>
                                </div>
                                <span class="oc-badge" style="background:{coul_score}; font-size:16px;">{score}%</span>
                            </div>
                            <div style="color:#8fa1b4; font-size:12px; margin-top:6px; font-weight:700;">{analyse_label}</div>
                            <div style="color:#b8c2cc; font-size:13px; margin-top:4px; font-style:italic;">
                                {h(profil_resume)}
                            </div>
                        </div>
                    """, unsafe_allow_html=True)

                    # Alertes
                    if res.get("alerte_habilitation"):
                        st.warning(f"⚠️ Habilitation : {res['alerte_habilitation']}")
                    if res.get("alerte_humaine"):
                        st.info(f"💡 Profil bénéficiaire : {res['alerte_humaine']}")

                    with st.expander(f"📋 Analyse détaillée — {nom_interv}"):

                        # ── Traits de personnalité (style OmniRecrut) ──
                        traits = res.get("traits_dominants", [])
                        if traits:
                            st.markdown("#### 🧠 Empreinte comportementale")
                            couleurs_traits = ["#2563eb", "#7c3aed", "#e0554f", "#d99a3d", "#3fae74"]
                            nb = min(len(traits), 5)
                            cols_traits = st.columns(nb)
                            for i, trait in enumerate(traits[:nb]):
                                lettre = str(trait).strip()[0].upper() if str(trait).strip() else "?"
                                mot_court = str(trait).strip().split()[0][:10]
                                with cols_traits[i]:
                                    st.markdown(f"""
                                        <div style="text-align:center; background:#1e293b; border-radius:12px;
                                                    padding:14px 8px; border:2px solid {couleurs_traits[i % len(couleurs_traits)]};">
                                            <div style="font-size:26px; font-weight:800;
                                                        color:{couleurs_traits[i % len(couleurs_traits)]};">{h(lettre)}</div>
                                            <div style="font-size:11px; color:#94a3b8;
                                                        margin-top:4px; font-weight:600;">{h(mot_court)}</div>
                                        </div>
                                    """, unsafe_allow_html=True)
                            st.markdown("")
                            for trait in traits:
                                st.markdown(f"""
                                    <div style="background:#1e293b; border-left:3px solid #2563eb;
                                                border-radius:6px; padding:10px 14px; margin-bottom:6px;
                                                color:#cbd5e1; font-size:13px;">🔹 {h(str(trait))}</div>
                                """, unsafe_allow_html=True)
                            st.markdown("---")

                        # ── Scores par dimension ──
                        st.markdown("#### 📊 Évaluation par dimension")
                        dimensions = list(OBJECTIVE_DIMENSIONS)
                        if ai_used:
                            dimensions += [d for d in AI_DIMENSIONS if isinstance(res.get(d[0]), int)]

                        for i in range(0, len(dimensions), 2):
                            paire = dimensions[i:i+2]
                            cols_dim = st.columns(len(paire))
                            for col_d, (cle, label, couleur) in zip(cols_dim, paire):
                                val = int(res.get(cle, 0) or 0)
                                nb_pleines = round(val / 20)
                                pastilles = "".join([
                                    f'<span style="display:inline-block; width:16px; height:16px; '
                                    f'border-radius:50%; margin-right:5px; '
                                    f'background:{couleur if j < nb_pleines else "#334155"};"></span>'
                                    for j in range(5)
                                ])
                                with col_d:
                                    st.markdown(f"""
                                        <div style="background:#1e293b; border-radius:10px; padding:14px 16px;
                                                    border-left:4px solid {couleur}; margin-bottom:10px; min-height:100px;">
                                            <div style="font-size:12px; color:#94a3b8; font-weight:600;
                                                        text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">
                                                {h(label)}
                                            </div>
                                            <div style="font-size:22px; font-weight:800; color:{couleur}; margin-bottom:8px;">
                                                {val}%
                                            </div>
                                            <div>{pastilles}</div>
                                        </div>
                                    """, unsafe_allow_html=True)
                        if not ai_used:
                            st.caption("Les dimensions humaines ne sont affichées que lorsqu'une analyse IA a réellement abouti.")
                        st.markdown("---")

                        # ── Compétences transférables ──
                        transf = res.get("competences_transferables", [])
                        if transf:
                            st.markdown("#### 🌱 Compétences transférables détectées")
                            for t in transf:
                                st.markdown(f"""
                                    <div style="background:#1e293b; border-left:3px solid #3fae74;
                                                border-radius:6px; padding:8px 12px; margin-bottom:5px;
                                                color:#86efac; font-size:12px;">✦ {h(str(t))}</div>
                                """, unsafe_allow_html=True)
                            st.markdown("---")

                        # ── Synthèse narrative ──
                        justif = res.get("justification", "")
                        if justif:
                            st.markdown("#### 📄 Synthèse de l'analyse")
                            st.markdown(f"""
                                <div style="background:#1a202c; padding:18px; border-radius:8px;
                                            color:#e2e8f0; white-space:pre-wrap; line-height:1.7;
                                            font-size:13px; border:1px solid rgba(47,124,246,0.3);">
                                    {h(justif)}
                                </div>
                            """, unsafe_allow_html=True)

                        # ── Export PDF du rapport ──
                        st.markdown("---")
                        benef_label_pdf = st.session_state.get("benef_matching_label", "Bénéficiaire")
                        try:
                            pdf_rapport = _generer_pdf_matching(
                                intervenant_nom=res.get("intervenant_nom", ""),
                                intervenant_statut=res.get("intervenant_statut", ""),
                                intervenant_zone=res.get("intervenant_zone", ""),
                                beneficiaire_nom=benef_label_pdf,
                                score_global=score,
                                profil_humain=res.get("profil_humain", ""),
                                traits_dominants=res.get("traits_dominants", []),
                                dimensions=dimensions,
                                scores=res,
                                competences_transferables=res.get("competences_transferables", []),
                                alerte_habilitation=res.get("alerte_habilitation", ""),
                                alerte_humaine=res.get("alerte_humaine", ""),
                                justification=res.get("justification", "")
                            )
                            st.download_button(
                                label="⬇️ Télécharger le rapport PDF",
                                data=pdf_rapport,
                                file_name=f"matching_{res.get('intervenant_nom','').replace(' ','_')}_{benef_label_pdf.replace(' ','_')}.pdf",
                                mime="application/pdf",
                                key=f"pdf_matching_{rang}"
                            )
                        except Exception as e_pdf:
                            logging.getLogger("omnicoord.ui").exception("PDF matching impossible", exc_info=e_pdf)
                            st.caption("Export PDF indisponible.")


    # ============================================================
    #  ❤️ BÉNÉFICIAIRES
    # ============================================================
    elif onglet == "❤️ Bénéficiaires":
        tab_liste_b, tab_ajout_b = st.tabs(["📋 Bénéficiaires", "➕ Ajouter"])

        df_interv_all = sb_select("intervenants", {"structure_id": SID}, order="nom")
        if not df_interv_all.empty and "deleted_at" in df_interv_all.columns:
            df_interv_all = df_interv_all[df_interv_all["deleted_at"].isna()]
        interv_map = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_interv_all.iterrows()}

        with tab_liste_b:
            df_b = sb_select("beneficiaires", {"structure_id": SID}, order="nom")
            if not df_b.empty and "deleted_at" in df_b.columns:
                df_b = df_b[df_b["deleted_at"].isna()]
            st.metric("Bénéficiaires actifs", len(df_b[df_b["statut"] == "Actif"]) if not df_b.empty else 0)

            if df_b.empty:
                st.info("Aucun bénéficiaire enregistré.")
            else:
                for _, row in df_b.iterrows():
                    coul = "#3fae74" if row["statut"] == "Actif" else "#8996a3"
                    attitré = h(interv_map.get(str(row.get("intervenant_attitré_id", "")), "Non défini"))
                    st.markdown(f"""
                        <div class="oc-card" style="border-left-color:{coul};">
                            <div style="display:flex; justify-content:space-between;">
                                <span style="font-size:17px; font-weight:700;">{h(row['prenom'])} {h(row['nom'])}</span>
                                <span class="oc-badge" style="background:{coul};">{h(row['statut'])}</span>
                            </div>
                            <div style="color:#b8c2cc; font-size:13px; margin-top:4px;">
                                📍 {h(row['adresse']) or '—'} &nbsp;•&nbsp; 🧑‍⚕️ {attitré}
                            </div>
                        </div>
                    """, unsafe_allow_html=True)

                    with st.expander(f"📋 Fiche — {row['prenom']} {row['nom']}"):
                        tab_vue_b, tab_edit_b = st.tabs(["👁️ Synthèse", "✏️ Modifier la fiche"])
                        with tab_vue_b:
                            c1, c2 = st.columns(2)
                            with c1:
                                st.markdown(f"""
                                    <div class="fiche-section"><h4>📍 Coordonnées</h4>
                                    <div class="fiche-row"><span class="fiche-label">Adresse</span><span class="fiche-value">{h(row['adresse']) or '—'}</span></div>
                                    <div class="fiche-row"><span class="fiche-label">Téléphone</span><span class="fiche-value">{h(row['telephone']) or '—'}</span></div>
                                    <div class="fiche-row"><span class="fiche-label">GIR</span><span class="fiche-value">{h(row['niveau_dependance']) or '—'}</span></div></div>
                                    <div class="fiche-section"><h4>🚨 Contact d'urgence</h4>
                                    <div class="fiche-row"><span class="fiche-label">Nom</span><span class="fiche-value">{h(row.get('contact_urgence_nom','')) or '—'}</span></div>
                                    <div class="fiche-row"><span class="fiche-label">Tél.</span><span class="fiche-value">{h(row.get('contact_urgence_tel','')) or '—'}</span></div></div>
                                """, unsafe_allow_html=True)
                            with c2:
                                st.markdown(f"""
                                    <div class="fiche-section"><h4>🔄 Besoins récurrents</h4><div class="fiche-value">{h(row.get('besoins_recurrents','')) or '—'}</div></div>
                                    <div class="fiche-section"><h4>🛠️ Gestes techniques</h4><div class="fiche-value">{h(row.get('gestes_techniques','')) or '—'}</div></div>
                                    <div class="fiche-section"><h4>🕐 Besoins horaires</h4><div class="fiche-value">{h(row.get('besoins_horaires','')) or '—'}</div></div>
                                    <div class="fiche-section"><h4>🧑‍⚕️ Intervenant attitré</h4><div class="fiche-value">{attitré}</div></div>
                                """, unsafe_allow_html=True)

                            if st.button("📥 Préparer export RGPD", key=f"rgpd_{row['id']}"):
                                df_iv_b = sb_select("interventions", {"structure_id": SID, "beneficiaire_id": str(row["id"])})
                                df_doc_b = sb_select("documents_transmissions", {"structure_id": SID, "beneficiaire_id": str(row["id"])})
                                st.session_state[f"rgpd_pdf_{row['id']}"] = creer_pdf_export_rgpd(row.to_dict(), df_iv_b.to_dict("records") if not df_iv_b.empty else [], df_doc_b.to_dict("records") if not df_doc_b.empty else [])
                                audit("EXPORT_RGPD", "beneficiaires", str(row["id"]))
                            if st.session_state.get(f"rgpd_pdf_{row['id']}"):
                                st.download_button("⬇️ Télécharger le dossier RGPD", data=st.session_state[f"rgpd_pdf_{row['id']}"], file_name=f"dossier_RGPD_{row['nom']}_{row['prenom']}.pdf", mime="application/pdf", key=f"dl_rgpd_{row['id']}")

                        with tab_edit_b:
                            with st.form(f"edit_benef_{row['id']}"):
                                b1, b2 = st.columns(2)
                                with b1:
                                    eb_nom = st.text_input("Nom *", value=str(row.get("nom") or ""), key=f"bn_{row['id']}")
                                    eb_prenom = st.text_input("Prénom *", value=str(row.get("prenom") or ""), key=f"bp_{row['id']}")
                                    eb_adresse = st.text_input("Adresse", value=str(row.get("adresse") or ""), key=f"ba_{row['id']}")
                                    eb_tel = st.text_input("Téléphone", value=str(row.get("telephone") or ""), key=f"bt_{row['id']}")
                                    girs = ["GIR 1","GIR 2","GIR 3","GIR 4","GIR 5","GIR 6","Non évalué"]
                                    gir_cur = row.get("niveau_dependance") if row.get("niveau_dependance") in girs else "Non évalué"
                                    eb_gir = st.selectbox("GIR", girs, index=girs.index(gir_cur), key=f"bg_{row['id']}")
                                    statuts_b = ["Actif","Inactif","Décédé"]
                                    stat_cur = row.get("statut") if row.get("statut") in statuts_b else "Actif"
                                    eb_statut = st.selectbox("Statut", statuts_b, index=statuts_b.index(stat_cur), key=f"bs_{row['id']}")
                                with b2:
                                    eb_cnom = st.text_input("Contact d'urgence (nom + lien)", value=str(row.get("contact_urgence_nom") or ""), key=f"bcn_{row['id']}")
                                    eb_ctel = st.text_input("Tél. contact d'urgence", value=str(row.get("contact_urgence_tel") or ""), key=f"bct_{row['id']}")
                                    eb_besoins = st.text_area("Besoins récurrents", value=str(row.get("besoins_recurrents") or ""), key=f"bb_{row['id']}")
                                    eb_gestes = st.text_area("Gestes techniques requis", value=str(row.get("gestes_techniques") or ""), key=f"bgest_{row['id']}")
                                    eb_horaires = st.text_input("Besoins horaires", value=str(row.get("besoins_horaires") or ""), key=f"bh_{row['id']}")
                                    eb_notes = st.text_area("Notes", value=str(row.get("notes") or ""), key=f"bnotes_{row['id']}")
                                opts_i = {"Non défini": None}
                                opts_i.update({v: k for k, v in interv_map.items()})
                                current_att_id = str(row.get("intervenant_attitré_id") or "")
                                current_att_label = next((lbl for lbl, iid in opts_i.items() if str(iid or "") == current_att_id), "Non défini")
                                eb_att = st.selectbox("Intervenant attitré", list(opts_i.keys()), index=list(opts_i.keys()).index(current_att_label), key=f"batt_{row['id']}")
                                save_b = st.form_submit_button("💾 Enregistrer les modifications")
                            if save_b:
                                if not eb_nom.strip() or not eb_prenom.strip():
                                    st.error("Le nom et le prénom sont obligatoires.")
                                else:
                                    p_b = {"nom": eb_nom.strip(), "prenom": eb_prenom.strip(), "adresse": eb_adresse.strip(), "telephone": eb_tel.strip(), "niveau_dependance": eb_gir, "statut": eb_statut, "contact_urgence_nom": eb_cnom.strip(), "contact_urgence_tel": eb_ctel.strip(), "besoins_recurrents": eb_besoins.strip(), "gestes_techniques": eb_gestes.strip(), "besoins_horaires": eb_horaires.strip(), "notes": eb_notes.strip(), "intervenant_attitré_id": opts_i[eb_att]}
                                    if sb_update("beneficiaires", p_b, "id", row["id"]):
                                        audit("UPDATE_BENEFICIAIRE", "beneficiaires", str(row["id"]), {"champs": list(p_b.keys())})
                                        st.success("Fiche bénéficiaire mise à jour.")
                                        st.rerun()

                            st.markdown("---")
                            confirm_b = st.checkbox("Confirmer l'archivage", key=f"confirm_del_b_{row['id']}", help="La fiche est retirée des listes actives mais l'historique est conservé.")
                            if st.button("🗑️ Archiver / retirer", key=f"del_b_{row['id']}", disabled=not confirm_b):
                                if sb_update("beneficiaires", {"deleted_at": datetime.datetime.utcnow().isoformat(), "statut": "Inactif"}, "id", row["id"]):
                                    audit("ARCHIVE_BENEFICIAIRE", "beneficiaires", str(row["id"]))
                                    st.success("Bénéficiaire archivé et retiré des listes actives.")
                                    st.rerun()

        with tab_ajout_b:
            with st.form("form_add_benef", clear_on_submit=True):
                c1, c2 = st.columns(2)
                with c1:
                    nom_b = st.text_input("Nom *")
                    prenom_b = st.text_input("Prénom *")
                    adresse_b = st.text_input("Adresse")
                    telephone_b = st.text_input("Téléphone")
                    niveau_dep = st.selectbox("GIR", ["GIR 1","GIR 2","GIR 3","GIR 4","GIR 5","GIR 6","Non évalué"])
                with c2:
                    contact_urgence_nom_b = st.text_input("Contact d'urgence (nom + lien)")
                    contact_urgence_tel_b = st.text_input("Tél. contact d'urgence")
                    besoins_rec = st.text_area("Besoins récurrents")
                    gestes_b = st.text_area("Gestes techniques requis")
                    horaires_b = st.text_input("Besoins horaires")
                    notes_b = st.text_area("Notes")

                opts_att = {"Non défini": None}
                if not df_interv_all.empty:
                    opts_att.update({f"{r['prenom']} {r['nom']}": str(r["id"]) for _, r in df_interv_all.iterrows()})
                att_sel = st.selectbox("Intervenant attitré (optionnel)", list(opts_att.keys()))

                if st.form_submit_button("Ajouter") and nom_b and prenom_b:
                    duplicate_b = False
                    if not df_b.empty:
                        duplicate_b = bool((
                            df_b["nom"].fillna("").astype(str).str.strip().str.casefold().eq(nom_b.strip().casefold())
                            & df_b["prenom"].fillna("").astype(str).str.strip().str.casefold().eq(prenom_b.strip().casefold())
                        ).any())
                    if duplicate_b:
                        st.error("Un bénéficiaire portant ce nom et ce prénom existe déjà dans la liste active.")
                    else:
                        new_b = sb_insert("beneficiaires", {
                        "structure_id": SID, "nom": nom_b.strip(), "prenom": prenom_b.strip(),
                        "adresse": adresse_b, "telephone": telephone_b, "niveau_dependance": niveau_dep,
                        "gestes_techniques": gestes_b, "besoins_horaires": horaires_b,
                        "besoins_recurrents": besoins_rec, "notes": notes_b,
                        "contact_urgence_nom": contact_urgence_nom_b, "contact_urgence_tel": contact_urgence_tel_b,
                        "intervenant_attitré_id": opts_att[att_sel],
                        "statut": "Actif", "date_creation": datetime.date.today().isoformat()
                        })
                        if new_b:
                            audit("CREATE_BENEFICIAIRE", "beneficiaires", new_b.get("id"))
                            st.success(f"{prenom_b} {nom_b} ajouté(e).")
                            st.rerun()


    # ============================================================
    #  📝 DOCUMENTS & TRANSMISSIONS
    # ============================================================
    elif onglet == "📝 Documents & Transmissions":
        df_benef2 = sb_select("beneficiaires", {"structure_id": SID, "statut": "Actif"}, order="nom")
        df_interv2 = sb_select("intervenants", {"structure_id": SID}, order="nom")

        if df_benef2.empty:
            st.info("Ajoutez d'abord un bénéficiaire.")
        else:
            c1, c2 = st.columns(2)
            benef_lbl2 = {f"{r['prenom']} {r['nom']}": str(r["id"]) for _, r in df_benef2.iterrows()}
            benef_ch2 = c1.selectbox("Bénéficiaire", list(benef_lbl2.keys()))
            interv_lbl2 = {"Non spécifié": None}
            interv_lbl2.update({f"{r['prenom']} {r['nom']}": str(r["id"]) for _, r in df_interv2.iterrows()})
            interv_ch2 = c2.selectbox("Intervenant rédacteur", list(interv_lbl2.keys()))

            type_doc = st.selectbox("Type", ["Fiche de liaison","Compte-rendu de visite","Transmission d'équipe","Note d'incident"])
            notes_brutes = st.text_area("Notes brutes", height=150)

            if st.button("✍️ Générer avec l'IA"):
                if not notes_brutes:
                    st.warning("Ajoutez des notes avant de générer.")
                else:
                    prompt = f"""
                    Tu es coordinateur(trice) en SAAD. Rédige un document de type "{type_doc}"
                    à partir des notes brutes, en style professionnel, factuel, 10-15 lignes max.
                    N'ajoute aucune information médicale absente des notes.

                    Bénéficiaire : {benef_ch2}
                    Notes : {notes_brutes}
                    """
                    texte_ia = appel_ia_texte(prompt)
                    if texte_ia is not None:
                        st.session_state["doc_genere"] = texte_ia

            if st.session_state.get("doc_genere"):
                texte_final = st.text_area("Document (modifiable)", value=st.session_state["doc_genere"], height=250)
                col_s, col_p = st.columns(2)
                with col_s:
                    if st.button("💾 Enregistrer"):
                        new_doc = sb_insert("documents_transmissions", {
                            "structure_id": SID,
                            "beneficiaire_id": benef_lbl2[benef_ch2],
                            "intervenant_id": interv_lbl2[interv_ch2],
                            "date_creation": datetime.date.today().isoformat(),
                            "type_document": type_doc,
                            "contenu": texte_final
                        })
                        if new_doc:
                            audit("CREATE_DOCUMENT", "documents_transmissions", new_doc.get("id"))
                            st.success("Document enregistré.")
                with col_p:
                    try:
                        pdf_b = creer_pdf_transmission(benef_ch2, interv_ch2, datetime.date.today().strftime("%d/%m/%Y"), texte_final)
                        st.download_button("⬇️ PDF", data=pdf_b, file_name=f"{type_doc}_{benef_ch2}.pdf", mime="application/pdf")
                    except Exception:
                        logger.exception("Génération PDF impossible")
                        st.error("Impossible de générer le PDF pour le moment.")

            st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
            st.markdown("### 🗂️ Historique")
            df_docs = sb_select("documents_transmissions", {"structure_id": SID}, order="date_creation")
            if df_docs.empty:
                st.caption("Aucun document enregistré.")
            else:
                st.dataframe(df_docs[["date_creation","type_document","contenu"]].head(50),
                            use_container_width=True, hide_index=True)


    # ============================================================
    #  📅 PLANNINGS & URGENCES
    # ============================================================
    elif onglet == "📅 Plannings & Urgences":
        tab_plan, tab_ajout_p, tab_urg = st.tabs(["📊 Planning hebdo", "➕ Planifier", "🚨 Urgences"])

        df_benef3 = sb_select("beneficiaires", {"structure_id": SID, "statut": "Actif"}, order="nom")
        if not df_benef3.empty and "deleted_at" in df_benef3.columns:
            df_benef3 = df_benef3[df_benef3["deleted_at"].isna()]
        df_interv3_raw = sb_select("intervenants", {"structure_id": SID}, order="nom")
        if not df_interv3_raw.empty and "deleted_at" in df_interv3_raw.columns:
            df_interv3_raw = df_interv3_raw[df_interv3_raw["deleted_at"].isna()]
        # Le planning ne doit jamais afficher deux lignes pour la même personne.
        # Les anciens IDs sont toutefois conservés afin de ne perdre aucune intervention historique.
        df_interv3 = _dedupe_intervenants_for_planning(df_interv3_raw)

        with tab_plan:
            if "semaine_offset" not in st.session_state:
                st.session_state["semaine_offset"] = 0

            cn1, cn2, cn3 = st.columns([1, 3, 1])
            if cn1.button("◀ Précédente"): st.session_state["semaine_offset"] -= 1; st.rerun()
            if cn3.button("Suivante ▶"): st.session_state["semaine_offset"] += 1; st.rerun()

            offset = st.session_state["semaine_offset"]
            aujourd = datetime.date.today()
            lundi = aujourd - datetime.timedelta(days=aujourd.weekday()) + datetime.timedelta(weeks=offset)
            dim = lundi + datetime.timedelta(days=6)

            cn2.markdown(f"<div style='text-align:center; color:#4c8dfa; font-weight:700;'>Semaine du {date_fr(lundi,'medium')} au {date_fr(dim,'medium')}</div>", unsafe_allow_html=True)
            if st.button("🔙 Semaine courante"): st.session_state["semaine_offset"] = 0; st.rerun()

            # Chargement des données de la semaine
            df_all_iv = sb_select("interventions", {"structure_id": SID})
            df_sem = pd.DataFrame()
            if not df_all_iv.empty:
                df_all_iv["date_intervention"] = pd.to_datetime(df_all_iv["date_intervention"]).dt.date
                df_sem = df_all_iv[(df_all_iv["date_intervention"] >= lundi) & (df_all_iv["date_intervention"] <= dim)]

            benef_noms = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_benef3.iterrows()}
            dates_sem = [lundi + datetime.timedelta(days=i) for i in range(7)]

            if df_interv3.empty:
                st.info("Ajoutez des intervenants pour voir le planning.")
            else:
                headers_html = '<th class="col-intervenant">Intervenant</th>'
                # Libellés explicites pour éviter qu'un navigateur/traducteur interprète
                # "Mar. 25/08" comme une date du type "25 mars 2008".
                jours_planning = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")
                for d in dates_sem:
                    is_today = (d == aujourd)
                    style = " style='background:rgba(47,124,246,0.25); color:#4c8dfa;'" if is_today else ""
                    libelle_jour = f"{jours_planning[d.weekday()]} {d.strftime('%d/%m')}"
                    headers_html += f'<th{style}>{libelle_jour}</th>'

                rows_html = ""
                for _, interv in df_interv3.iterrows():
                    row_html = f'<td class="col-intervenant">{h(interv["prenom"])} {h(interv["nom"])}</td>'
                    for d in dates_sem:
                        if df_sem.empty:
                            row_html += '<td><div class="planning-empty">·</div></td>'
                            continue
                        interv_ids = [str(x) for x in (interv.get("_intervenant_ids") or [interv["id"]])]
                        ivs = df_sem[
                            (df_sem["date_intervention"] == d) &
                            (df_sem["intervenant_id"].astype(str).isin(interv_ids))
                        ]
                        if ivs.empty:
                            row_html += '<td><div class="planning-empty">·</div></td>'
                        else:
                            cell = ""
                            for _, iv in ivs.iterrows():
                                css = " urgence" if iv["statut"]=="Urgence à pourvoir" else (" realise" if iv["statut"]=="Réalisé" else (" annule" if iv["statut"]=="Annulé" else ""))
                                b = h(benef_noms.get(str(iv.get("beneficiaire_id","")), "—"))
                                cell += f'<div class="planning-cell{css}"><b>{h(str(iv["heure_debut"]))}–{h(str(iv["heure_fin"]))}</b><br>{b}<br><span style="color:#8996a3;font-size:11px;">{h(str(iv["type_intervention"]))}</span></div>'
                            row_html += f'<td>{cell}</td>'
                    rows_html += f"<tr>{row_html}</tr>"

                st.markdown(f'<div style="overflow-x:auto;"><table class="planning-table"><thead><tr>{headers_html}</tr></thead><tbody>{rows_html}</tbody></table></div>', unsafe_allow_html=True)

        with tab_ajout_p:
            if df_benef3.empty or df_interv3.empty:
                st.info("Ajoutez au moins un bénéficiaire et un intervenant.")
            else:
                df_habs_plan = sb_select("habilitations", {"structure_id": SID})
                hab_catalog = CANONICAL_HABILITATIONS
                hab_options = set(hab_catalog)
                if not df_habs_plan.empty and "type_habilitation" in df_habs_plan.columns:
                    hab_options.update(canonicalize_habilitation(v) for v in df_habs_plan["type_habilitation"].dropna().astype(str).tolist())
                hab_options = sorted(hab_options)
                with st.form("form_plan", clear_on_submit=True):
                    c1, c2, c3 = st.columns(3)
                    benef_lbl3 = {f"{r['prenom']} {r['nom']}": str(r["id"]) for _, r in df_benef3.iterrows()}
                    interv_lbl3 = {f"{r['prenom']} {r['nom']}": str(r["id"]) for _, r in df_interv3.iterrows()}

                    benef_placeholder = "— Sélectionner un bénéficiaire —"
                    interv_unassigned = "À pourvoir / Non affecté"
                    benef_p = c1.selectbox("Bénéficiaire", [benef_placeholder] + list(benef_lbl3.keys()), index=0)
                    interv_p = c2.selectbox("Intervenant", [interv_unassigned] + list(interv_lbl3.keys()), index=0)
                    type_iv = c3.selectbox("Type", ["Aide à la toilette","Aide au repas","Ménage","Accompagnement","Soins","Autre"])
                    c4, c5, c6 = st.columns(3)
                    date_p = c4.date_input("Date", value=datetime.date.today())
                    hd = c5.time_input("Heure début")
                    hf = c6.time_input("Heure fin")
                    habs_requises = st.multiselect(
                        "Habilitations obligatoires pour cette mission",
                        hab_options,
                        help="Si une habilitation est sélectionnée, une personne qui ne la possède pas ou dont elle est expirée sera écartée uniquement de cette mission et signalée en conformité.",
                    )
                    notes_p = st.text_input("Notes")

                    if st.form_submit_button("Planifier"):
                        if benef_p == benef_placeholder:
                            st.error("Sélectionnez d'abord un bénéficiaire.")
                        elif hf <= hd:
                            st.error("L'heure de fin doit être après l'heure de début.")
                        else:
                            try:
                                intervenant_id = None if interv_p == interv_unassigned else interv_lbl3[interv_p]
                                duplicates = find_duplicate_interventions(
                                    SID, benef_lbl3[benef_p], date_p, hd, hf, type_iv
                                )
                                if not duplicates.empty:
                                    raise ValidationError("Une intervention identique existe déjà pour ce bénéficiaire, à cette date et sur ce créneau.")
                                if intervenant_id:
                                    ensure_no_intervenant_conflict(
                                        SID,
                                        intervenant_id,
                                        date_p,
                                        hd,
                                        hf,
                                    )
                                statut_iv = "Urgence à pourvoir" if intervenant_id is None else "Planifié"
                                new_iv = sb_insert("interventions", {
                                    "structure_id": SID,
                                    "beneficiaire_id": benef_lbl3[benef_p],
                                    "intervenant_id": intervenant_id,
                                    "date_intervention": date_p.isoformat(),
                                    "heure_debut": hd.strftime("%H:%M"),
                                    "heure_fin": hf.strftime("%H:%M"),
                                    "type_intervention": type_iv,
                                    "statut": statut_iv,
                                    "notes": encode_required_habilitations(notes_p, habs_requises)
                                })
                                if new_iv:
                                    audit("CREATE_INTERVENTION", "interventions", new_iv.get("id"), {"statut": statut_iv})
                                    if intervenant_id is None:
                                        st.success("Intervention créée comme À pourvoir. Elle est disponible dans l'onglet Urgences.")
                                    else:
                                        st.success("Intervention planifiée.")
                                    st.rerun()
                            except ValidationError as exc:
                                st.error(str(exc))
                            except DatabaseError:
                                st.error("Impossible de vérifier les conflits de planning.")

            # Interventions à venir
            st.markdown("### 📋 Interventions à venir")
            df_plan = sb_select("interventions", {"structure_id": SID}, order="date_intervention")
            if not df_plan.empty:
                df_plan["date_intervention"] = pd.to_datetime(df_plan["date_intervention"]).dt.date
                df_plan = df_plan[df_plan["date_intervention"] >= datetime.date.today()]
                benef_noms2 = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_benef3.iterrows()}
                interv_noms2 = {}
                for _, r in df_interv3.iterrows():
                    display_name = f"{r['prenom']} {r['nom']}"
                    for rid in (r.get("_intervenant_ids") or [r["id"]]):
                        interv_noms2[str(rid)] = display_name

                for _, row in df_plan.iterrows():
                    coul = {"Planifié":"#4c8dfa","Urgence à pourvoir":"#e0554f","Réalisé":"#3fae74","Annulé":"#8996a3"}.get(row["statut"], "#8996a3")
                    b = h(benef_noms2.get(str(row.get("beneficiaire_id","")), "—"))
                    iv = h(interv_noms2.get(str(row.get("intervenant_id","")), "Non assigné"))
                    ci, ca = st.columns([4, 1])
                    with ci:
                        st.markdown(f"""
                            <div class="oc-card" style="border-left-color:{coul}; padding:12px 16px;">
                                <b>{row['date_intervention']} — {h(str(row['heure_debut']))} à {h(str(row['heure_fin']))}</b> · {h(str(row['type_intervention']))}<br>
                                <span style="color:#b8c2cc;">👤 {b} • 🧑‍⚕️ {iv} •
                                <span class="oc-badge" style="background:{coul}; padding:2px 10px;">{h(str(row['statut']))}</span></span>
                            </div>
                        """, unsafe_allow_html=True)
                    with ca:
                        if row["statut"] == "Planifié":
                            if st.button("✅ Réalisée", key=f"r_{row['id']}"):
                                sb_update("interventions", {"statut": "Réalisé"}, "id", str(row["id"]))
                                st.rerun()
                        if row["statut"] not in ["Urgence à pourvoir","Annulé","Réalisé"]:
                            if st.button("🚨 Absence", key=f"a_{row['id']}"):
                                sb_update("interventions", {"statut": "Urgence à pourvoir", "intervenant_id": None}, "id", str(row["id"]))
                                st.rerun()

        with tab_urg:
            st.caption("OmniCoord filtre d'abord les contraintes bloquantes, puis classe les personnes réellement affectables.")
            df_urgs = sb_select("interventions", {"structure_id": SID, "statut": "Urgence à pourvoir"}, order="date_intervention")

            if df_urgs.empty:
                st.success("✅ Aucune urgence en cours.")
            else:
                benef_by_id = {str(r["id"]): r.to_dict() for _, r in df_benef3.iterrows()}
                for _, urg in df_urgs.iterrows():
                    urg_dict = urg.to_dict()
                    benef = benef_by_id.get(str(urg.get("beneficiaire_id", "")), {})
                    b = h(f"{benef.get('prenom', '')} {benef.get('nom', '')}".strip() or "Inconnu")
                    req = required_habilitations(urg_dict)
                    req_html = " • ".join(h(x) for x in req) if req else "Aucune habilitation obligatoire déclarée"
                    st.markdown(f"""
                        <div class="oc-card oc-card-alert">
                            <b>🚨 {h(str(urg['date_intervention']))} — {h(str(urg['heure_debut']))} à {h(str(urg['heure_fin']))}</b><br>
                            {b} • {h(str(urg['type_intervention']))}<br>
                            <span style="color:#b8c2cc;">🎓 {req_html}</span>
                        </div>
                    """, unsafe_allow_html=True)

                    key_results = f"replacement_results_{urg['id']}"
                    if st.button("🔎 Trouver les meilleurs remplaçants", key=f"find_rep_{urg['id']}"):
                        try:
                            with st.spinner("Analyse du vivier et des contraintes..."):
                                st.session_state[key_results] = rank_replacements(SID, urg_dict, benef, df_interv3)
                        except DatabaseError:
                            st.error("Impossible de charger les données nécessaires au remplacement.")

                    results = st.session_state.get(key_results, [])
                    eligible = [r for r in results if r.get("eligible")]
                    blocked = [r for r in results if not r.get("eligible")]

                    if results:
                        st.markdown("#### ✅ Affectables maintenant")
                        if not eligible:
                            st.warning("Aucun intervenant n'est actuellement affectable à cette mission.")
                        for rank, cand in enumerate(eligible[:5], 1):
                            cinfo, caction = st.columns([4, 1])
                            with cinfo:
                                warnings = cand.get("warnings") or []
                                warn_html = "<br>".join(f"🟠 {h(w)}" for w in warnings)
                                st.markdown(f"""
                                    <div class="oc-card">
                                        <b>#{rank} — {h(cand['intervenant_nom'])}</b>
                                        <span class="oc-badge" style="background:#3fae74; margin-left:8px;">{cand['score_global']}%</span><br>
                                        <span style="color:#b8c2cc;">{h(str(cand.get('type_statut','')))} • {h(str(cand.get('zone_geo','') or 'Zone non précisée'))}</span><br>
                                        <span style="font-size:13px;">Compétences {cand['score_competences']}% • Zone {cand['score_zone']}%</span>
                                        {('<br>'+warn_html) if warn_html else ''}
                                    </div>
                                """, unsafe_allow_html=True)
                            with caction:
                                if st.button("Affecter", key=f"assign_{urg['id']}_{cand['intervenant_id']}", type="primary"):
                                    try:
                                        ok, blockers, warnings = validate_replacement_candidate(SID, urg_dict, cand["intervenant_id"])
                                        if not ok:
                                            st.error("Affectation refusée : " + " ; ".join(blockers))
                                        else:
                                            updated = sb_update(
                                                "interventions",
                                                {"intervenant_id": cand["intervenant_id"], "statut": "Planifié"},
                                                "id",
                                                str(urg["id"]),
                                            )
                                            if updated:
                                                audit(
                                                    "ASSIGN_REPLACEMENT",
                                                    "interventions",
                                                    str(urg["id"]),
                                                    {
                                                        "intervenant_id": cand["intervenant_id"],
                                                        "score": cand["score_global"],
                                                        "warnings": warnings,
                                                    },
                                                )
                                                st.session_state.pop(key_results, None)
                                                st.success(f"✅ {cand['intervenant_nom']} affecté(e). Planning mis à jour.")
                                                st.rerun()
                                    except DatabaseError:
                                        st.error("Impossible de revalider l'affectation. Réessayez.")

                        if blocked:
                            with st.expander(f"🚫 Écartés pour cette mission ({len(blocked)})"):
                                st.caption("Ces personnes restent dans le vivier. Elles sont seulement non éligibles à cette intervention.")
                                for cand in blocked:
                                    reasons = " • ".join(h(x) for x in cand.get("blockers", []))
                                    st.markdown(
                                        f"<div class='oc-card oc-card-warning'><b>{h(cand['intervenant_nom'])}</b> — {h(str(cand.get('type_statut','')))}<br>🚫 {reasons}</div>",
                                        unsafe_allow_html=True,
                                    )

                    st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)


    # ============================================================
    #  ✅ CONFORMITÉ & HABILITATIONS
    # ============================================================
    elif onglet == "✅ Conformité & Habilitations":
        tab_suivi, tab_ajout_hab = st.tabs(["📋 Suivi", "➕ Ajouter"])

        df_interv4 = sb_select("intervenants", {"structure_id": SID}, order="nom")
        if not df_interv4.empty and "deleted_at" in df_interv4.columns:
            df_interv4 = df_interv4[df_interv4["deleted_at"].isna()]
        aujourd = datetime.date.today()
        seuil = aujourd + datetime.timedelta(days=60)

        with tab_suivi:
            df_habs = sb_select("habilitations", {"structure_id": SID})
            if df_habs.empty:
                st.info("Aucune habilitation enregistrée.")
            else:
                interv_noms4 = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_interv4.iterrows()}
                df_habs["intervenant_nom"] = df_habs["intervenant_id"].apply(lambda x: interv_noms4.get(str(x), "Inconnu"))
                df_habs["type_canonique"] = df_habs["type_habilitation"].apply(canonicalize_habilitation)
                df_habs["date_exp_dt"] = pd.to_datetime(df_habs["date_expiration"], errors="coerce").dt.date
                df_habs["_date_obt_sort"] = pd.to_datetime(df_habs["date_obtention"], errors="coerce")

                # Le suivi synthétique ne doit pas compter les anciennes lignes d'un
                # renouvellement comme des alertes actuelles. On conserve tout l'historique
                # en base, mais on affiche ici uniquement la saisie la plus récente par
                # intervenant + type canonique.
                current_habs = (
                    df_habs.sort_values(
                        ["intervenant_id", "type_canonique", "_date_obt_sort"],
                        ascending=[True, True, False],
                        na_position="last",
                    )
                    .drop_duplicates(["intervenant_id", "type_canonique"], keep="first")
                    .copy()
                )

                exp = current_habs[current_habs["date_exp_dt"].notna() & (current_habs["date_exp_dt"] < aujourd)]
                bientot = current_habs[current_habs["date_exp_dt"].notna() & (current_habs["date_exp_dt"] >= aujourd) & (current_habs["date_exp_dt"] <= seuil)]
                permanent = current_habs[current_habs["date_exp_dt"].isna() | (current_habs["date_exp_dt"] == NO_EXPIRY_DATE)]
                ok = pd.concat([current_habs[(current_habs["date_exp_dt"] > seuil) & (current_habs["date_exp_dt"] != NO_EXPIRY_DATE)], permanent], ignore_index=False)

                c1, c2, c3 = st.columns(3)
                c1.metric("🔴 Expirées", len(exp))
                c2.metric("🟠 < 60 jours", len(bientot))
                c3.metric("🟢 À jour", len(ok))

                if not exp.empty:
                    st.markdown("#### 🔴 Expirées")
                    for _, hb in exp.iterrows():
                        st.markdown(f'<div class="oc-card oc-card-alert"><b>{h(hb["intervenant_nom"])}</b> — {h(hb["type_canonique"])} — expirée le {hb["date_expiration"]}</div>', unsafe_allow_html=True)
                if not bientot.empty:
                    st.markdown("#### 🟠 À renouveler bientôt")
                    for _, hb in bientot.iterrows():
                        st.markdown(f'<div class="oc-card oc-card-warning"><b>{h(hb["intervenant_nom"])}</b> — {h(hb["type_canonique"])} — expire le {hb["date_expiration"]}</div>', unsafe_allow_html=True)
                if not ok.empty:
                    with st.expander("🟢 À jour"):
                        ok_display = ok[["intervenant_nom","type_canonique","date_obtention","date_expiration"]].copy()
                        ok_display = ok_display.rename(columns={"type_canonique": "type_habilitation"})
                        ok_display["date_expiration"] = ok_display["date_expiration"].apply(
                            lambda v: "Valide sans date d'expiration" if (pd.isna(v) or str(v)[:10] == "9999-12-31") else v
                        )
                        st.dataframe(ok_display, use_container_width=True, hide_index=True)

        with tab_ajout_hab:
            if df_interv4.empty:
                st.info("Ajoutez d'abord un intervenant.")
            else:
                validite_mode = st.radio(
                    "Validité",
                    ["Avec date d'expiration", "Valide sans date d'expiration"],
                    horizontal=True,
                    key="hab_validite_mode",
                )
                with st.form("form_hab", clear_on_submit=True):
                    interv_lbl4 = {f"{r['prenom']} {r['nom']}": str(r["id"]) for _, r in df_interv4.iterrows()}
                    interv_sel = st.selectbox("Intervenant", list(interv_lbl4.keys()))
                    type_hab = st.selectbox("Type", CANONICAL_HABILITATIONS)
                    date_obt = st.date_input("Date d'obtention")
                    date_exp = None
                    if validite_mode == "Avec date d'expiration":
                        date_exp = st.date_input("Date d'expiration")

                    if st.form_submit_button("Ajouter"):
                        if date_exp is not None and date_exp <= date_obt:
                            st.error("La date d'expiration doit être après la date d'obtention.")
                        else:
                            target_id = interv_lbl4[interv_sel]
                            existing_records = []
                            if not df_habs.empty and "intervenant_id" in df_habs.columns:
                                existing_records = df_habs[df_habs["intervenant_id"].astype(str) == str(target_id)].to_dict("records")
                            duplicate_reason = duplicate_habilitation_reason(
                                existing_records, type_hab, date_obt, permanent=date_exp is None
                            )
                            if duplicate_reason:
                                st.warning(duplicate_reason)
                            else:
                                new_h = sb_insert("habilitations", {
                                    "structure_id": SID,
                                    "intervenant_id": target_id,
                                    "type_habilitation": canonicalize_habilitation(type_hab),
                                    "date_obtention": date_obt.isoformat(),
                                    # Sentinelle rétrocompatible : évite d'exiger une migration si
                                    # date_expiration est NOT NULL dans une base déjà déployée.
                                    "date_expiration": date_exp.isoformat() if date_exp is not None else NO_EXPIRY_DATE.isoformat(),
                                })
                                if new_h:
                                    audit("CREATE_HABILITATION", "habilitations", new_h.get("id"), {"sans_expiration": date_exp is None, "type_canonique": canonicalize_habilitation(type_hab)})
                                    st.success("Habilitation ajoutée.")
                                    st.rerun()


    # ============================================================
    #  📊 SUIVI DES HEURES (NOUVEAU MODULE)
    # ============================================================
    elif onglet == "📊 Suivi des heures":
        st.caption("Suivi mensuel des heures planifiées vs réalisées par intervenant.")

        df_interv_h = sb_select("intervenants", {"structure_id": SID}, order="nom")
        df_iv_all = sb_select("interventions", {"structure_id": SID})

        if df_interv_h.empty or df_iv_all.empty:
            st.info("Aucune donnée disponible. Planifiez des interventions.")
        else:
            df_iv_all["date_intervention"] = pd.to_datetime(df_iv_all["date_intervention"])
            df_iv_all["mois"] = df_iv_all["date_intervention"].dt.to_period("M")
            df_iv_all["duree_h"] = df_iv_all.apply(
                lambda r: (
                    datetime.datetime.strptime(str(r["heure_fin"]), "%H:%M:%S") -
                    datetime.datetime.strptime(str(r["heure_debut"]), "%H:%M:%S")
                ).seconds / 3600 if pd.notna(r["heure_debut"]) and pd.notna(r["heure_fin"]) else 0,
                axis=1
            )

            interv_noms_h = {str(r["id"]): f"{r['prenom']} {r['nom']}" for _, r in df_interv_h.iterrows()}
            df_iv_all["intervenant_nom"] = df_iv_all["intervenant_id"].apply(lambda x: interv_noms_h.get(str(x), "Non assigné"))

            # Filtre mois
            mois_dispo = df_iv_all["mois"].dropna().unique()
            mois_dispo_str = sorted([str(m) for m in mois_dispo], reverse=True)
            mois_sel = st.selectbox("Mois", mois_dispo_str) if mois_dispo_str else None

            if mois_sel:
                df_mois = df_iv_all[df_iv_all["mois"].astype(str) == mois_sel]
                df_resume = df_mois.groupby("intervenant_nom").agg(
                    nb_interventions=("id", "count"),
                    heures_planifiees=("duree_h", "sum"),
                    heures_realisees=("duree_h", lambda x: x[df_mois.loc[x.index, "statut"] == "Réalisé"].sum())
                ).reset_index()

                st.markdown(f"### Mois de {mois_sel}")
                col_t1, col_t2, col_t3 = st.columns(3)
                col_t1.metric("Total interventions", int(df_resume["nb_interventions"].sum()))
                col_t2.metric("Heures planifiées", f"{df_resume['heures_planifiees'].sum():.1f}h")
                col_t3.metric("Heures réalisées", f"{df_resume['heures_realisees'].sum():.1f}h")

                st.dataframe(
                    df_resume.rename(columns={
                        "intervenant_nom": "Intervenant",
                        "nb_interventions": "Nb interventions",
                        "heures_planifiees": "Heures planifiées",
                        "heures_realisees": "Heures réalisées"
                    }),
                    use_container_width=True, hide_index=True
                )

                # Export CSV
                csv = df_resume.to_csv(index=False).encode("utf-8")
                st.download_button("⬇️ Exporter en CSV", data=csv,
                                   file_name=f"heures_{mois_sel}.csv", mime="text/csv")


    # ============================================================
    #  👤 MON PROFIL — Version admin / version client
    # ============================================================
    elif onglet == "👤 Mon Profil":

        if IS_ADMIN:
            # -------------------------------------------------------
            #  PROFIL ADMIN = Éditeur SaaS
            #  Il configure uniquement sa boîte mail pour envoyer
            #  les identifiants à ses clients.
            # -------------------------------------------------------
            st.caption("👑 Compte administrateur OmniCoord IA — Éditeur SaaS")

            st.subheader("🔑 Changer mon mot de passe admin")
            with st.form("form_mdp_admin"):
                n1 = st.text_input("Nouveau mot de passe", type="password")
                n2 = st.text_input("Confirmer", type="password")
                if st.form_submit_button("Mettre à jour"):
                    if not n1 or n1 != n2:
                        st.error("Les mots de passe ne correspondent pas.")
                    elif len(n1) < 8:
                        st.error("8 caractères minimum.")
                    else:
                        try:
                            sb.auth.update_user({"password": n1})
                            audit("CHANGE_PASSWORD", "profils", USER_ID)
                            st.success("Mot de passe mis à jour.")
                        except Exception:
                            logger.exception("Mise à jour du mot de passe impossible")
                            st.error("Impossible d'effectuer cette opération pour le moment.")

            st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
            st.subheader("📧 Ma boîte mail (envoi des accès clients)")
            st.info("💡 Cette boîte sert à envoyer automatiquement les identifiants à vos nouveaux clients. Utilisez un **mot de passe d'application Gmail** (pas votre mot de passe personnel). Générez-en un sur myaccount.google.com > Sécurité > Mots de passe des applications.")

            with st.form("form_mail_admin"):
                cfg = st.session_state.get("mail_config", {})
                mail_e = st.text_input("Adresse e-mail d'envoi", value=cfg.get("email", ""))
                mail_p = st.text_input("Mot de passe d'application Gmail (16 caractères)", type="password",
                                        help="Chiffré avec Fernet avant stockage en base.")
                mail_i = st.text_input("Serveur IMAP", value=cfg.get("imap", "imap.gmail.com"))

                if st.form_submit_button("Enregistrer"):
                    if mail_p and len(mail_p) not in [16, 19]:
                        st.warning("Un mot de passe d'application Gmail fait normalement 16 caractères.")
                    mdp_chiffre = chiffrer_mdp_mail(mail_p) if mail_p else ""
                    update_data = {"mail_smtp_email": mail_e, "mail_imap_server": mail_i}
                    if mdp_chiffre:
                        update_data["mail_smtp_password"] = mdp_chiffre
                    if sb_update("profils", update_data, "id", USER_ID):
                        st.session_state["mail_config"] = {
                            "email": mail_e,
                            "imap": mail_i
                        }
                        audit("UPDATE_MAIL_CONFIG", "profils", USER_ID)
                        st.success("✅ Configuration mail enregistrée (mot de passe chiffré).")

            # Test de connexion mail
            st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
            st.subheader("🧪 Tester l'envoi de mail")
            email_test = st.text_input("Envoyer un email de test à :")
            if st.button("Envoyer le test") and email_test:
                ok, msg = envoyer_email(
                    email_test,
                    "Test OmniCoord IA — Configuration mail OK",
                    "Bonjour,\n\nCeci est un email de test envoyé depuis OmniCoord IA.\n\nSi vous recevez ce message, votre configuration mail est opérationnelle et vous pouvez envoyer les identifiants à vos clients.\n\nOmniCoord IA"
                )
                if ok: st.success(f"✅ {msg}")
                else: st.error(f"❌ {msg}")

        else:
            # -------------------------------------------------------
            #  PROFIL CLIENT = SAAD / SSIAD
            #  Il configure sa boîte mail pour envoyer les
            #  sollicitations d'urgence à ses intervenants.
            # -------------------------------------------------------
            st.caption(f"Structure : **{st.session_state.get('structure_nom', '—')}**")

            st.subheader("🔑 Changer mon mot de passe")
            with st.form("form_mdp"):
                n1 = st.text_input("Nouveau mot de passe", type="password")
                n2 = st.text_input("Confirmer", type="password")
                if st.form_submit_button("Mettre à jour"):
                    if not n1 or n1 != n2:
                        st.error("Les mots de passe ne correspondent pas.")
                    elif len(n1) < 8:
                        st.error("8 caractères minimum.")
                    else:
                        try:
                            sb.auth.update_user({"password": n1})
                            audit("CHANGE_PASSWORD", "profils", USER_ID)
                            st.success("Mot de passe mis à jour.")
                        except Exception:
                            logger.exception("Mise à jour du mot de passe impossible")
                            st.error("Impossible d'effectuer cette opération pour le moment.")

            st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
            st.subheader("📧 Ma boîte mail (sollicitations intervenants)")
            st.info("💡 Gmail : utilisez un **mot de passe d'application** (pas votre mot de passe principal). Générez-en un sur myaccount.google.com > Sécurité > Mots de passe des applications.")

            with st.form("form_mail"):
                cfg = st.session_state.get("mail_config", {})
                mail_e = st.text_input("Adresse e-mail d'envoi", value=cfg.get("email", ""))
                mail_p = st.text_input("Mot de passe d'application Gmail (16 caractères)", type="password",
                                        help="Ce mot de passe est chiffré avant d'être stocké.")
                mail_i = st.text_input("Serveur IMAP", value=cfg.get("imap", "imap.gmail.com"))

                if st.form_submit_button("Enregistrer"):
                    if mail_p and len(mail_p) not in [16, 19]:
                        st.warning("Un mot de passe d'application Gmail fait normalement 16 caractères.")
                    mdp_chiffre = chiffrer_mdp_mail(mail_p) if mail_p else ""
                    update_data = {"mail_smtp_email": mail_e, "mail_imap_server": mail_i}
                    if mdp_chiffre:
                        update_data["mail_smtp_password"] = mdp_chiffre
                    if sb_update("profils", update_data, "id", USER_ID):
                        st.session_state["mail_config"] = {
                            "email": mail_e,
                            "imap": mail_i
                        }
                        audit("UPDATE_MAIL_CONFIG", "profils", USER_ID)
                        st.success("✅ Configuration mail enregistrée (mot de passe chiffré).")

            # Test de connexion mail
            st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
            st.subheader("🧪 Tester la connexion mail")
            email_test = st.text_input("Envoyer un email de test à :")
            if st.button("Envoyer le test") and email_test:
                ok, msg = envoyer_email(
                    email_test,
                    "Test OmniCoord IA — Connexion mail OK",
                    "Bonjour,\n\nCeci est un email de test envoyé depuis OmniCoord IA.\nSi vous recevez ce message, la configuration mail est correcte.\n\nOmniCoord IA"
                )
                if ok: st.success(f"✅ {msg}")
                else: st.error(f"❌ {msg}")


    # ============================================================
    #  🛠️ ADMINISTRATION — Cockpit commercial SaaS
    #  Visible uniquement par l'admin (éditeur OmniCoord)
    # ============================================================
    elif onglet == "🛠️ Administration" and IS_ADMIN:

        APP_URL = "https://omnicoord-ia-bxgnddxxgniwnhhu9pmo9r.streamlit.app"

        tab_clients, tab_creer, tab_quotas, tab_audit, tab_secu = st.tabs([
            "📋 Mes clients",
            "➕ Créer un accès client",
            "📊 Quotas IA",
            "📋 Journal d'audit",
            "🔐 Sécurité"
        ])

        # ----------------------------------------------------------
        #  TAB 1 : LISTE DES CLIENTS
        # ----------------------------------------------------------
        with tab_clients:
            st.subheader("📋 Mes clients — Vue d'ensemble")

            df_structs_admin = sb_select("structures", order="nom")
            df_users_admin = sb_select("profils", order="email")

            # Exclure l'admin de la liste des clients
            df_clients = df_users_admin[df_users_admin["est_admin"] == False].copy() if not df_users_admin.empty else pd.DataFrame()

            # Métriques commerciales
            col_c1, col_c2, col_c3, col_c4 = st.columns(4)
            nb_clients_total = len(df_clients)
            nb_actifs = 0
            nb_expires = 0
            nb_essai = 0
            if not df_clients.empty:
                df_clients["date_fin_dt"] = pd.to_datetime(df_clients["date_fin_essai"], errors="coerce").dt.date
                aujourdhui = datetime.date.today()
                nb_actifs = len(df_clients[df_clients["date_fin_dt"] >= aujourdhui])
                nb_expires = len(df_clients[df_clients["date_fin_dt"] < aujourdhui])
                nb_essai = len(df_clients[df_clients["statut_abonnement"] == "ESSAI"])

            col_c1.metric("👥 Total clients", nb_clients_total)
            col_c2.metric("✅ Actifs", nb_actifs)
            col_c3.metric("⏰ En essai", nb_essai)
            col_c4.metric("🔴 Expirés", nb_expires, delta=f"-{nb_expires}" if nb_expires > 0 else None, delta_color="inverse")

            st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)

            if df_clients.empty:
                st.info("Aucun client créé pour l'instant. Utilisez l'onglet « Créer un accès client » pour commencer.")
            else:
                # Récupérer les noms de structure
                struct_noms = {}
                if not df_structs_admin.empty:
                    struct_noms = {str(r["id"]): r["nom"] for _, r in df_structs_admin.iterrows()}

                for _, client in df_clients.iterrows():
                    struct_nom = h(struct_noms.get(str(client.get("structure_id", "")), "Non assignée"))
                    email_c = h(client["email"])
                    statut_c = client["statut_abonnement"]
                    date_fin_c = client.get("date_fin_essai", "—")

                    # Couleur selon statut
                    if client.get("date_fin_dt") and client["date_fin_dt"] < datetime.date.today():
                        couleur = "#e0554f"
                        badge = "Expiré"
                    elif statut_c == "PRO":
                        couleur = "#3fae74"
                        badge = "PRO"
                    else:
                        couleur = "#d99a3d"
                        badge = "Essai"

                    st.markdown(f"""
                        <div class="oc-card" style="border-left-color:{couleur};">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <span style="font-size:16px; font-weight:700;">🏢 {struct_nom}</span>
                                <span class="oc-badge" style="background:{couleur};">{badge}</span>
                            </div>
                            <div style="color:#b8c2cc; font-size:13px; margin-top:6px;">
                                📧 {email_c} &nbsp;|&nbsp; 📅 Accès jusqu'au {h(str(date_fin_c))} &nbsp;|&nbsp;
                                🤖 IA : {client.get('nb_requetes_ia', 0)}/{client.get('quota_max_ia', 20)}
                            </div>
                        </div>
                    """, unsafe_allow_html=True)

                    with st.expander(f"⚙️ Gérer — {email_c}"):
                        col_g1, col_g2, col_g3 = st.columns(3)

                        with col_g1:
                            # Prolonger l'accès
                            jours_prolonger = st.number_input("Prolonger (jours)", min_value=1, value=30, key=f"prol_{client['id']}")
                            if st.button("📅 Prolonger l'accès", key=f"btn_prol_{client['id']}"):
                                nouvelle_fin = (datetime.date.today() + datetime.timedelta(days=int(jours_prolonger))).isoformat()
                                if sb_update("profils", {"date_fin_essai": nouvelle_fin}, "id", str(client["id"])):
                                    audit("PROLONGER_ACCES", "profils", str(client["id"]), {"nouvelle_fin": nouvelle_fin})
                                    st.success(f"Accès prolongé jusqu'au {date_fr(nouvelle_fin, 'court')}")
                                    st.rerun()

                        with col_g2:
                            # Changer le statut d'abonnement
                            nv_statut = st.selectbox("Statut abonnement", ["ESSAI", "PRO", "SUSPENDU"],
                                                      index=["ESSAI", "PRO", "SUSPENDU"].index(statut_c) if statut_c in ["ESSAI", "PRO", "SUSPENDU"] else 0,
                                                      key=f"stat_{client['id']}")
                            if st.button("💳 Mettre à jour le statut", key=f"btn_stat_{client['id']}"):
                                if sb_update("profils", {"statut_abonnement": nv_statut}, "id", str(client["id"])):
                                    audit("UPDATE_ABONNEMENT", "profils", str(client["id"]), {"statut": nv_statut})
                                    st.success(f"Statut mis à jour → {nv_statut}")
                                    st.rerun()

                        with col_g3:
                            # Modifier le quota IA
                            nv_quota = st.number_input("Quota IA max", min_value=1, value=int(client.get("quota_max_ia", 20)), key=f"quota_{client['id']}")
                            if st.button("🤖 Modifier le quota", key=f"btn_quota_{client['id']}"):
                                if sb_update("profils", {"quota_max_ia": int(nv_quota)}, "id", str(client["id"])):
                                    audit("UPDATE_QUOTA", "profils", str(client["id"]), {"quota": nv_quota})
                                    st.success(f"Quota IA mis à jour → {nv_quota}")
                                    st.rerun()

                        st.markdown("<br>", unsafe_allow_html=True)

                        # Remettre le quota IA à zéro
                        if st.button("🔄 Remettre le compteur IA à 0", key=f"reset_{client['id']}"):
                            if sb_update("profils", {"nb_requetes_ia": 0}, "id", str(client["id"])):
                                st.success("Compteur IA remis à zéro.")
                                st.rerun()

                        # Renvoyer les identifiants par mail
                        if st.button("📧 Renvoyer les identifiants par mail", key=f"remail_{client['id']}"):
                            ok, msg = envoyer_email(
                                client["email"],
                                "OmniCoord IA — Vos identifiants de connexion",
                                f"Bonjour,\n\n"
                                f"Voici vos identifiants pour accéder à OmniCoord IA :\n\n"
                                f"🔗 Lien : {APP_URL}\n"
                                f"📧 Email : {client['email']}\n"
                                f"📅 Accès valable jusqu'au : {date_fin_c}\n\n"
                                f"Si vous avez oublié votre mot de passe, contactez l'administrateur.\n\n"
                                f"Cordialement,\nOmniCoord IA"
                            )
                            if ok: st.success(f"✅ Email envoyé à {client['email']}")
                            else: st.error(f"❌ {msg}")

                        # Supprimer le client
                        st.markdown("<div class='oc-metal-divider'></div>", unsafe_allow_html=True)
                        confirm_del = st.checkbox(f"Je confirme la suppression de {email_c} et toutes ses données", key=f"confirm_{client['id']}")
                        if st.button("🗑️ Supprimer définitivement ce client", key=f"del_{client['id']}"):
                            if confirm_del:
                                try:
                                    # Supprimer dans Supabase Auth
                                    delete_auth_user(str(client["id"]))
                                    # Supprimer le profil (les données métier restent liées à la structure)
                                    sb_delete("profils", "id", str(client["id"]))
                                    audit("DELETE_CLIENT", "profils", str(client["id"]), {"email": client["email"]})
                                    st.success(f"Client {client['email']} supprimé.")
                                    st.rerun()
                                except Exception:
                                    logger.exception("Suppression du client impossible")
                                    st.error("Impossible de supprimer ce client pour le moment.")
                            else:
                                st.warning("Cochez la case de confirmation.")

        # ----------------------------------------------------------
        #  TAB 2 : CRÉER UN ACCÈS CLIENT
        # ----------------------------------------------------------
        with tab_creer:
            st.subheader("➕ Créer un accès client")
            st.caption("Crée un compte pour un nouveau client. Un email avec le lien, l'identifiant et le mot de passe lui sera envoyé automatiquement.")

            with st.form("form_creer_client"):
                col_c1, col_c2 = st.columns(2)
                with col_c1:
                    nom_structure = st.text_input("Nom de la structure (SAAD / SSIAD) *")
                    email_client = st.text_input("Email du client *")
                    mdp_client = st.text_input("Mot de passe temporaire *")
                with col_c2:
                    duree_acces = st.number_input("Durée d'accès (jours)", min_value=1, value=30)
                    statut_abo = st.selectbox("Type d'abonnement", ["ESSAI", "PRO"])
                    quota_ia = st.number_input("Quota IA (nombre de requêtes)", min_value=1, value=20)

                envoyer_mail_auto = st.checkbox("📧 Envoyer automatiquement les identifiants par email", value=True)
                btn_creer = st.form_submit_button("🚀 Créer l'accès client")

                if btn_creer:
                    if not nom_structure or not email_client or not mdp_client:
                        st.error("Tous les champs avec * sont obligatoires.")
                    elif len(mdp_client) < 8:
                        st.error("Le mot de passe doit faire au moins 8 caractères.")
                    else:
                        # 1. Créer la structure
                        struct_existante = sb_select("structures", {"nom": nom_structure.strip()})
                        if struct_existante.empty:
                            struct_res = sb_insert("structures", {"nom": nom_structure.strip()})
                            if struct_res:
                                struct_id = struct_res["id"]
                            else:
                                struct_id = None
                        else:
                            struct_id = struct_existante.iloc[0]["id"]

                        if struct_id:
                            date_fin = (datetime.date.today() + datetime.timedelta(days=int(duree_acces))).isoformat()
                            try:
                                # 2. Créer l'utilisateur dans Supabase Auth
                                auth_res = create_auth_user(email_client, mdp_client)
                                new_uid = auth_res.user.id

                                # 3. Créer le profil
                                sb_insert("profils", {
                                    "id": new_uid,
                                    "structure_id": struct_id,
                                    "email": email_client.strip().lower(),
                                    "est_admin": False,
                                    "statut_abonnement": statut_abo,
                                    "quota_max_ia": int(quota_ia),
                                    "date_fin_essai": date_fin
                                })
                                audit("CREATE_CLIENT", "profils", new_uid, {
                                    "structure": nom_structure, "email": email_client, "duree": duree_acces
                                })

                                st.success(f"✅ Accès créé pour **{email_client}** (structure : {nom_structure}) jusqu'au **{date_fr(date_fin, 'court')}**")

                                # 4. Envoyer les identifiants par mail (si coché)
                                if envoyer_mail_auto:
                                    ok, msg = envoyer_email(
                                        email_client,
                                        "Bienvenue sur OmniCoord IA — Vos identifiants",
                                        f"Bonjour,\n\n"
                                        f"Votre accès à OmniCoord IA a été créé avec succès.\n\n"
                                        f"🔗 Lien de connexion : {APP_URL}\n\n"
                                        f"📧 Identifiant : {email_client}\n"
                                        f"🔑 Mot de passe : {mdp_client}\n\n"
                                        f"📅 Votre accès est valable jusqu'au {date_fr(date_fin, 'court')}.\n\n"
                                        f"⚠️ Nous vous recommandons de changer votre mot de passe dès votre première connexion (Mon Profil > Changer mon mot de passe).\n\n"
                                        f"Pour toute question, contactez-nous.\n\n"
                                        f"Cordialement,\n"
                                        f"L'équipe OmniCoord IA"
                                    )
                                    if ok:
                                        st.success(f"📧 Email envoyé à {email_client}")
                                    else:
                                        st.warning(f"⚠️ Accès créé mais email non envoyé : {msg}")
                                        st.info(f"Identifiants à transmettre manuellement :\n- Lien : {APP_URL}\n- Email : {email_client}\n- Mot de passe : {mdp_client}")
                                else:
                                    st.info(f"📋 Identifiants à transmettre manuellement :\n- **Lien** : {APP_URL}\n- **Email** : {email_client}\n- **Mot de passe** : {mdp_client}")

                            except Exception as e:
                                err_msg = str(e)
                                if "already been registered" in err_msg or "already exists" in err_msg:
                                    st.error("Cet email est déjà utilisé par un autre compte.")
                                else:
                                    logger.exception("Création client impossible")
                                    st.error("Erreur lors de la création du client. Réessayez ou consultez les journaux.")

        # ----------------------------------------------------------
        #  TAB 3 : QUOTAS IA
        # ----------------------------------------------------------
        with tab_quotas:
            st.subheader("📊 Quotas IA — Vue globale")
            df_users_q = sb_select("profils", order="email")
            if not df_users_q.empty:
                df_clients_q = df_users_q[df_users_q["est_admin"] == False].copy()
                if not df_clients_q.empty:
                    df_clients_q["usage_%"] = (df_clients_q["nb_requetes_ia"] / df_clients_q["quota_max_ia"] * 100).round(1)
                    st.dataframe(
                        df_clients_q[["email", "nb_requetes_ia", "quota_max_ia", "usage_%", "statut_abonnement"]].rename(columns={
                            "email": "Client",
                            "nb_requetes_ia": "Utilisées",
                            "quota_max_ia": "Quota max",
                            "usage_%": "Utilisation %",
                            "statut_abonnement": "Abonnement"
                        }),
                        use_container_width=True, hide_index=True
                    )
                else:
                    st.info("Aucun client créé.")
            else:
                st.info("Aucune donnée disponible.")

        # ----------------------------------------------------------
        #  TAB 4 : JOURNAL D'AUDIT
        # ----------------------------------------------------------
        with tab_audit:
            st.subheader("📋 Journal d'audit — 50 dernières actions")
            df_audit = sb_select("audit_logs", order="created_at")
            if not df_audit.empty:
                df_audit_aff = df_audit.sort_values("created_at", ascending=False).head(50)
                st.dataframe(
                    df_audit_aff[["created_at", "action", "table_name", "record_id"]].rename(columns={
                        "created_at": "Date",
                        "action": "Action",
                        "table_name": "Table",
                        "record_id": "Enregistrement"
                    }),
                    use_container_width=True, hide_index=True
                )
            else:
                st.caption("Aucune action enregistrée.")

        # ----------------------------------------------------------
        #  TAB 5 : SÉCURITÉ
        # ----------------------------------------------------------
        with tab_secu:
            st.subheader("🔐 État de la sécurité")
            st.markdown("""
            - ✅ **Auth** : Supabase Auth (bcrypt natif + JWT)
            - 🟡 **RLS** : Cloisonnement par structure actif — audit SQL complet fourni avec la V3
            - ✅ **Mots de passe mail** : Chiffrés avec Fernet avant stockage
            - ✅ **Anti brute-force** : Blocage après 10 échecs / 15 min
            - ✅ **Injection HTML** : html.escape() sur toutes les valeurs injectées
            - ✅ **Audit log** : Toutes les actions sensibles tracées
            - ✅ **PDF** : Unicode natif (fpdf2), plus de caractères manquants
            - ✅ **Quota IA** : Re-vérifié en base à chaque appel (pas en session)
            """)
