"""Exceptions applicatives OmniCoord IA."""


class OmniCoordError(Exception):
    """Erreur métier/technique générique de l'application."""


class DatabaseError(OmniCoordError):
    """Erreur d'accès à Supabase/PostgreSQL."""


class AuthenticationError(OmniCoordError):
    """Erreur d'authentification."""


class AuthorizationError(OmniCoordError):
    """Action interdite pour l'utilisateur courant."""


class ValidationError(OmniCoordError):
    """Donnée ou règle métier invalide."""


class AIServiceError(OmniCoordError):
    """Erreur du fournisseur IA ou de parsing de sa réponse."""


class QuotaExceededError(AIServiceError):
    """Quota IA épuisé."""


class EmailServiceError(OmniCoordError):
    """Erreur de configuration ou d'envoi d'e-mail."""
