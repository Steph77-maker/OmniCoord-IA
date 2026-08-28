"""Intégration Stripe Billing pour OmniCoord IA.

Le retour navigateur de Stripe ne modifie jamais directement ``statut_abonnement``.
Les changements d'accès payant seront appliqués par le webhook Stripe, qui restera
la source de vérité pour les abonnements.

La clé secrète Stripe reste exclusivement côté serveur dans ``st.secrets``.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

import streamlit as st

from .database import current_authenticated_user_id, get_supabase_admin

logger = logging.getLogger("omnicoord.stripe")

STRIPE_API_BASE = "https://api.stripe.com/v1"
DEFAULT_APP_URL = "https://omnicoord-ia-bxgnddxxgniwnhhu9pmo9r.streamlit.app"
SUPPORTED_PLANS = {"PRO", "PRO_PLUS"}


class BillingError(RuntimeError):
    """Erreur fonctionnelle de facturation présentable à l'utilisateur."""


class BillingConfigurationError(BillingError):
    """Configuration Stripe serveur absente ou incohérente."""


def _secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
    except Exception:
        value = default
    return str(value or "").strip()


def stripe_is_configured() -> bool:
    """True si la clé Stripe et les deux Price IDs nécessaires sont présents."""
    return bool(
        _secret("STRIPE_SECRET_KEY")
        and _secret("STRIPE_PRICE_PRO")
        and _secret("STRIPE_PRICE_PRO_PLUS")
    )


def _require_config() -> tuple[str, dict[str, str], str]:
    secret_key = _secret("STRIPE_SECRET_KEY")
    prices = {
        "PRO": _secret("STRIPE_PRICE_PRO"),
        "PRO_PLUS": _secret("STRIPE_PRICE_PRO_PLUS"),
    }
    app_url = _secret("STRIPE_APP_URL", DEFAULT_APP_URL).rstrip("/")

    if not secret_key:
        raise BillingConfigurationError("Clé secrète Stripe absente de la configuration serveur.")
    if not prices["PRO"] or not prices["PRO_PLUS"]:
        raise BillingConfigurationError("Price IDs Stripe PRO / PRO PLUS absents de la configuration serveur.")
    if not app_url.startswith("https://"):
        raise BillingConfigurationError("URL publique OmniCoord invalide pour les retours Stripe.")
    return secret_key, prices, app_url


def _stripe_post(path: str, params: dict[str, object], *, idempotency_key: str | None = None) -> dict:
    secret_key, _, _ = _require_config()
    encoded = urllib.parse.urlencode(params, doseq=True).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "OmniCoord-IA/1.0",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key[:255]

    request = urllib.request.Request(
        f"{STRIPE_API_BASE}/{path.lstrip('/')}",
        data=encoded,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise BillingError("Réponse Stripe inattendue.")
            return payload
    except urllib.error.HTTPError as exc:
        # Ne jamais journaliser les en-têtes : ils contiennent la clé Bearer.
        try:
            body = json.loads(exc.read().decode("utf-8"))
            stripe_message = str((body.get("error") or {}).get("message") or "")
        except Exception:
            stripe_message = ""
        logger.error("Stripe API HTTP %s sur %s", exc.code, path)
        if stripe_message:
            raise BillingError(f"Stripe a refusé l'opération : {stripe_message}") from exc
        raise BillingError("Stripe a refusé l'opération de facturation.") from exc
    except urllib.error.URLError as exc:
        logger.error("Stripe API inaccessible sur %s: %s", path, type(exc.reason).__name__)
        raise BillingError("Stripe est momentanément inaccessible. Réessayez dans quelques instants.") from exc
    except TimeoutError as exc:
        logger.error("Timeout Stripe API sur %s", path)
        raise BillingError("Stripe met trop de temps à répondre. Réessayez dans quelques instants.") from exc


def _current_billing_context() -> tuple[str, str, str, dict]:
    """Revalide l'identité et la structure avant toute opération privilégiée Stripe."""
    user_id = current_authenticated_user_id()
    if not user_id:
        raise BillingError("Session utilisateur invalide. Reconnectez-vous.")

    admin = get_supabase_admin()
    try:
        profile_res = (
            admin.table("profils")
            .select("structure_id,email,est_admin")
            .eq("id", user_id)
            .single()
            .execute()
        )
        profile = profile_res.data or {}
        if not profile or bool(profile.get("est_admin", False)):
            raise BillingError("Cette opération est réservée à un compte client OmniCoord.")

        structure_id = str(profile.get("structure_id") or "").strip()
        if not structure_id:
            raise BillingError("Aucune structure n'est associée à ce compte.")
        session_sid = str(st.session_state.get("structure_id") or "").strip()
        if session_sid and session_sid != structure_id:
            raise BillingError("Contexte de structure incohérent. Reconnectez-vous.")

        structure_res = (
            admin.table("structures")
            .select(
                "id,nom,stripe_customer_id,stripe_subscription_id,"
                "stripe_subscription_status,stripe_price_id,stripe_current_period_end"
            )
            .eq("id", structure_id)
            .single()
            .execute()
        )
        structure = structure_res.data or {}
        if not structure:
            raise BillingError("Structure OmniCoord introuvable.")

        email = str(profile.get("email") or st.session_state.get("user_email") or "").strip().lower()
        return user_id, structure_id, email, structure
    except BillingError:
        raise
    except Exception as exc:
        logger.exception("Impossible de revalider le contexte de facturation")
        raise BillingError("Impossible de vérifier le compte avant la facturation.") from exc


def get_billing_state() -> dict:
    """Retourne uniquement l'état Stripe de la structure courante."""
    _, _, _, structure = _current_billing_context()
    return {
        "stripe_customer_id": structure.get("stripe_customer_id"),
        "stripe_subscription_id": structure.get("stripe_subscription_id"),
        "stripe_subscription_status": structure.get("stripe_subscription_status"),
        "stripe_price_id": structure.get("stripe_price_id"),
        "stripe_current_period_end": structure.get("stripe_current_period_end"),
    }


def _ensure_customer(structure_id: str, email: str, structure: dict) -> str:
    customer_id = str(structure.get("stripe_customer_id") or "").strip()
    if customer_id:
        return customer_id

    params: dict[str, object] = {
        "name": str(structure.get("nom") or "Structure OmniCoord"),
        "metadata[omnicoord_structure_id]": structure_id,
    }
    if email:
        params["email"] = email

    customer = _stripe_post(
        "customers",
        params,
        idempotency_key=f"omnicoord-customer-{structure_id}",
    )
    customer_id = str(customer.get("id") or "").strip()
    if not customer_id:
        raise BillingError("Stripe n'a pas retourné d'identifiant client.")

    try:
        get_supabase_admin().table("structures").update({
            "stripe_customer_id": customer_id
        }).eq("id", structure_id).execute()
    except Exception as exc:
        logger.exception("Impossible d'enregistrer le customer Stripe")
        raise BillingError("Client Stripe créé mais liaison OmniCoord impossible. Contactez l'administrateur.") from exc
    return customer_id


def create_checkout_session(plan: str) -> str:
    """Crée un Checkout Stripe hébergé pour PRO ou PRO_PLUS et retourne son URL."""
    normalized_plan = str(plan or "").strip().upper().replace(" ", "_")
    if normalized_plan not in SUPPORTED_PLANS:
        raise BillingError("Formule Stripe inconnue.")

    _, prices, app_url = _require_config()
    user_id, structure_id, email, structure = _current_billing_context()

    existing_subscription = str(structure.get("stripe_subscription_id") or "").strip()
    existing_status = str(structure.get("stripe_subscription_status") or "").strip().lower()
    if existing_subscription and existing_status not in {"canceled", "incomplete_expired", "unpaid"}:
        raise BillingError("Un abonnement Stripe existe déjà pour cette structure. Utilisez le portail client.")

    customer_id = _ensure_customer(structure_id, email, structure)
    price_id = prices[normalized_plan]
    minute_bucket = int(time.time() // 60)

    params: dict[str, object] = {
        "mode": "subscription",
        "customer": customer_id,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": 1,
        "client_reference_id": structure_id,
        "metadata[omnicoord_structure_id]": structure_id,
        "metadata[omnicoord_user_id]": user_id,
        "metadata[omnicoord_plan]": normalized_plan,
        "subscription_data[metadata][omnicoord_structure_id]": structure_id,
        "subscription_data[metadata][omnicoord_plan]": normalized_plan,
        "success_url": f"{app_url}?stripe=success&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{app_url}?stripe=cancel",
    }

    session = _stripe_post(
        "checkout/sessions",
        params,
        idempotency_key=f"omnicoord-checkout-{structure_id}-{normalized_plan}-{minute_bucket}",
    )
    url = str(session.get("url") or "").strip()
    if not url.startswith("https://"):
        raise BillingError("Stripe n'a pas retourné d'URL de paiement valide.")
    return url


def create_portal_session() -> str:
    """Crée une session du Customer Portal Stripe pour la structure courante."""
    _, _, app_url = _require_config()
    _, _, _, structure = _current_billing_context()
    customer_id = str(structure.get("stripe_customer_id") or "").strip()
    if not customer_id:
        raise BillingError("Aucun compte Stripe n'est encore lié à cette structure.")

    session = _stripe_post(
        "billing_portal/sessions",
        {
            "customer": customer_id,
            "return_url": f"{app_url}?stripe=portal_return",
        },
    )
    url = str(session.get("url") or "").strip()
    if not url.startswith("https://"):
        raise BillingError("Stripe n'a pas retourné d'URL de portail valide.")
    return url
