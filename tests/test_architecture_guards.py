"""Tests statiques légers qui empêchent le retour de failles supprimées en phase 2."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "omnicoord"


def test_gemini_direct_calls_are_centralized():
    offenders = []
    for path in PKG.glob("*.py"):
        if path.name in {"ai_service.py", "legacy_app.py"}:
            continue
        if "model.generate_content(" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert offenders == [], f"Appels Gemini directs hors ai_service.py: {offenders}"


def test_user_supabase_client_is_not_global_cache_resource():
    source = (PKG / "database.py").read_text(encoding="utf-8")
    marker = "def get_supabase()"
    pos = source.index(marker)
    prefix = source[max(0, pos - 80):pos]
    assert "@st.cache_resource" not in prefix


def test_smtp_password_not_written_to_session_state_in_ui():
    source = (PKG / "ui.py").read_text(encoding="utf-8")
    assert '"password": mail_p' not in source


def test_document_generation_does_not_bypass_ai_service():
    source = (PKG / "ui.py").read_text(encoding="utf-8")
    assert "appel_ia_texte(prompt)" in source
    assert "model.generate_content(prompt)" not in source


def test_matching_score_is_computed_in_python():
    source = (PKG / "matching_service.py").read_text(encoding="utf-8")
    assert 'result["score_global"] = round(' in source
    assert '"score_global": <' not in source


def test_planning_has_overlap_guard():
    source = (PKG / "planning_service.py").read_text(encoding="utf-8")
    assert "ensure_no_intervenant_conflict" in source
    assert "sa < eb and sb < ea" in source
