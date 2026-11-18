"""Tests des garde-fous de risque."""

from __future__ import annotations

import pytest

from bot.risque import GestionRisque, Limites, RisqueRefuse

LIMITES = Limites(
    montant_max_position=5.0,
    fraction_risque_par_trade=0.01,
    perte_max_journaliere=30.0,
    trades_max_par_jour=3,
)


@pytest.fixture()
def gestion():
    return GestionRisque(LIMITES, capital_initial=1000.0)


def test_trade_conforme_accepte(gestion):
    gestion.valider(5.0, capital=1000.0)


def test_montant_au_dessus_du_plafond_refuse(gestion):
    with pytest.raises(RisqueRefuse, match="plafond"):
        gestion.valider(6.0, capital=1000.0)


def test_budget_limite_par_la_fraction_de_capital(gestion):
    # 1 % de 200 $ = 2 $ -> le budget ne peut pas dépasser 2 $.
    assert gestion.budget_trade(capital=200.0) == 2.0
    with pytest.raises(RisqueRefuse, match="budget"):
        gestion.valider(3.0, capital=200.0)


def test_quota_journalier(gestion):
    for _ in range(3):
        gestion.valider(1.0, capital=1000.0)
        gestion.enregistrer_trade(1.0)
    with pytest.raises(RisqueRefuse, match="Quota"):
        gestion.valider(1.0, capital=1000.0)


def test_perte_journaliere_maximale(gestion):
    gestion.enregistrer_resultat(-29.0)
    with pytest.raises(RisqueRefuse, match="Perte journalière"):
        gestion.valider(2.0, capital=1000.0)


def test_perte_encore_toleree_sous_le_seuil(gestion):
    gestion.enregistrer_resultat(-20.0)
    gestion.valider(5.0, capital=1000.0)


def test_capital_insuffisant_refuse(gestion):
    with pytest.raises(RisqueRefuse, match="insuffisant|budget"):
        gestion.valider(5.0, capital=2.0)


def test_montant_negatif_refuse(gestion):
    with pytest.raises(RisqueRefuse, match="invalide"):
        gestion.valider(-1.0, capital=1000.0)


def test_series_perdantes_suivies(gestion):
    for _ in range(3):
        gestion.enregistrer_resultat(-1.0)
    assert gestion.etat.series_perdantes == 3
    gestion.enregistrer_resultat(0.87)
    assert gestion.etat.series_perdantes == 0


def test_instantane_risque_est_serialisable(gestion):
    gestion.enregistrer_trade(1.0)
    gestion.enregistrer_resultat(-1.0)
    instantane = gestion.etat.instantane(LIMITES, 999.0)
    assert instantane["trades_du_jour"] == 1
    assert instantane["perte_consommee"] == 1.0
    assert instantane["perte_restante"] == 29.0
    assert 0 <= instantane["pct_perte_utilisee"] <= 100
