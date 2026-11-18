"""Tests de la configuration : la sécurité doit être vérifiable."""

from __future__ import annotations

import pytest

from bot.config import ConfigError, charger_config


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Environnement propre : aucun .env sur disque, variables contrôlées."""
    for cle in ("MODE_REEL", "SSID", "CONFIRMATION_REELLE", "EMA_RAPIDE", "EMA_LENTE"):
        monkeypatch.delenv(cle, raising=False)
    return tmp_path / ".env"


def test_refuse_de_demarrer_sans_mode_reel(env):
    with pytest.raises(ConfigError, match="MODE_REEL"):
        charger_config(env)


def test_mode_demo_par_defaut(env, monkeypatch):
    monkeypatch.setenv("MODE_REEL", "False")
    settings = charger_config(env)
    assert settings.mode == "DEMO"
    assert settings.mode_reel is False


def test_valeur_de_mode_invalide(env, monkeypatch):
    monkeypatch.setenv("MODE_REEL", "peut-etre")
    with pytest.raises(ConfigError, match="invalide"):
        charger_config(env)


def test_reel_sans_ssid_refuse(env, monkeypatch):
    monkeypatch.setenv("MODE_REEL", "True")
    with pytest.raises(ConfigError, match="SSID"):
        charger_config(env)


def test_reel_sans_double_confirmation_refuse(env, monkeypatch):
    monkeypatch.setenv("MODE_REEL", "True")
    monkeypatch.setenv("SSID", "abc123session")
    with pytest.raises(ConfigError, match="verrouillé"):
        charger_config(env)


def test_reel_avec_confirmation_accepte(env, monkeypatch):
    monkeypatch.setenv("MODE_REEL", "True")
    monkeypatch.setenv("SSID", "abcdefghijklmnop")
    monkeypatch.setenv("CONFIRMATION_REELLE", "JE CONFIRME LE RISQUE REEL")
    settings = charger_config(env)
    assert settings.mode == "REEL"


def test_le_ssid_n_apparait_jamais_en_clair(env, monkeypatch):
    monkeypatch.setenv("MODE_REEL", "False")
    monkeypatch.setenv("SSID", "super-secret-session-token")
    settings = charger_config(env)
    assert "super-secret-session-token" not in repr(settings)
    assert "super-secret-session-token" not in str(settings.resume_public())
    assert settings.ssid_masque() == "supe…oken"


def test_ema_incoherentes_refusees(env, monkeypatch):
    monkeypatch.setenv("MODE_REEL", "False")
    monkeypatch.setenv("EMA_RAPIDE", "50")
    monkeypatch.setenv("EMA_LENTE", "20")
    with pytest.raises(ConfigError, match="EMA_RAPIDE"):
        charger_config(env)


def test_montant_superieur_au_plafond_refuse(env, monkeypatch):
    monkeypatch.setenv("MODE_REEL", "False")
    monkeypatch.setenv("MONTANT_TRADE", "10")
    monkeypatch.setenv("TAILLE_MAX_POSITION", "2")
    with pytest.raises(ConfigError, match="TAILLE_MAX_POSITION"):
        charger_config(env)
