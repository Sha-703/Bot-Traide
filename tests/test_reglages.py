"""Tests des réglages modifiables à chaud.

On vérifie surtout les BORNES : une faute de frappe dans la console ne doit
jamais produire une mise absurde (0 $, 10 000 $) ou désactiver un garde-fou.
"""

from __future__ import annotations

import pytest

from bot.config import Settings
from bot.reglages import ReglageInvalide, Reglages


def _settings(**extra) -> Settings:
    base = dict(mode_reel=False, capital_initial=1000.0)
    base.update(extra)
    return Settings(**base)


def _reglages() -> Reglages:
    return Reglages.depuis_settings(_settings())


# --------------------------------------------------------------------------- #
#  Valeurs par défaut et sérialisation
# --------------------------------------------------------------------------- #
def test_depuis_settings_reprend_les_valeurs_du_env():
    r = Reglages.depuis_settings(
        _settings(montant_trade=2.0, taille_max_position=7.0, payout=0.9)
    )
    assert r.montant_trade == 2.0
    assert r.taille_max_position == 7.0
    assert r.payout == 0.9
    assert r.mode == "DEMO"


def test_instantane_expose_des_pourcentages_lisibles():
    vue = _reglages().instantane()
    assert vue["fraction_risque_pct"] == pytest.approx(1.0)
    assert vue["payout_pct"] == pytest.approx(87.0)
    assert vue["bornes"]["montant_trade"] == [0.01, 1000.0]
    assert "OBSERVATION" in vue["modes_disponibles"]


# --------------------------------------------------------------------------- #
#  Validation stricte
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "patch, attendu",
    [
        ({"montant_trade": 0}, "montant_trade"),
        ({"montant_trade": -5}, "montant_trade"),
        ({"montant_trade": 5000}, "montant_trade"),
        ({"trades_max_par_jour": 0}, "trades_max_par_jour"),
        ({"trades_max_par_jour": 2.5}, "entier"),
        ({"duree_expiration_s": 2}, "duree_expiration_s"),
        ({"fraction_risque_pct": 50}, "fraction_risque_pct"),
        ({"payout_pct": 120}, "payout_pct"),
        ({"montant_trade": "abc"}, "nombre"),
    ],
)
def test_valeurs_hors_bornes_refusees(patch, attendu):
    with pytest.raises(ReglageInvalide) as err:
        _reglages().mis_a_jour(patch)
    assert attendu in str(err.value)


def test_la_mise_ne_peut_pas_depasser_le_plafond():
    with pytest.raises(ReglageInvalide, match="plafond"):
        _reglages().mis_a_jour({"montant_trade": 10.0, "taille_max_position": 5.0})


def test_perte_max_doit_couvrir_au_moins_un_trade():
    with pytest.raises(ReglageInvalide, match="perte journalière"):
        _reglages().mis_a_jour({"montant_trade": 4.0, "perte_max_journaliere": 2.0})


def test_pourcentage_converti_en_fraction():
    r = _reglages().mis_a_jour({"fraction_risque_pct": 2.5, "payout_pct": 91})
    assert r.fraction_risque_par_trade == pytest.approx(0.025)
    assert r.payout == pytest.approx(0.91)


def test_virgule_decimale_acceptee():
    r = _reglages().mis_a_jour({"montant_trade": "2,50"})
    assert r.montant_trade == pytest.approx(2.5)


def test_mode_inconnu_refuse():
    with pytest.raises(ReglageInvalide, match="Mode inconnu"):
        _reglages().mis_a_jour({"mode": "YOLO"})


def test_mode_normalise_en_majuscules():
    assert _reglages().mis_a_jour({"mode": "observation"}).mode == "OBSERVATION"


def test_mise_a_jour_partielle_conserve_le_reste():
    base = _reglages()
    r = base.mis_a_jour({"trades_max_par_jour": 42})
    assert r.trades_max_par_jour == 42
    assert r.montant_trade == base.montant_trade
    # L'instance d'origine ne doit pas être modifiée (copie explicite).
    assert base.trades_max_par_jour != 42


def test_copier_depuis_modifie_en_place():
    cible = _reglages()
    source = cible.mis_a_jour({"montant_trade": 3.0, "mode": "OBSERVATION"})
    identite = id(cible)
    cible.copier_depuis(source)
    assert id(cible) == identite
    assert cible.montant_trade == 3.0
    assert cible.mode == "OBSERVATION"


# --------------------------------------------------------------------------- #
#  Intégration moteur : les limites du risque suivent les réglages
# --------------------------------------------------------------------------- #
def test_journal_recharge_son_historique(tmp_path):
    from bot.journalisation import Journal, TradeJournalise

    journal = Journal(tmp_path)
    for i in range(3):
        journal.enregistrer(
            TradeJournalise(
                horodatage_utc="2026-11-17T12:00:00+00:00",
                mode="DEMO",
                actif="EURUSD",
                direction="ACHAT",
                montant=1.0,
                expiration_s=60,
                prix_entree=1.08 + i * 0.001,
                prix_sortie=1.09 + i * 0.001,
                resultat="GAGNANT",
                pnl=0.87,
                solde_apres=1000.0 + (i + 1) * 0.87,
                strategie="EMA9/21",
            )
        )

    relu = Journal(tmp_path)
    assert len(relu.trades) == 3
    assert relu.trades[0].actif == "EURUSD"
    assert relu.trades[-1].solde_apres == pytest.approx(1002.61)
    assert relu.statistiques()["total"] == 3


def test_journal_ignore_les_lignes_invalides(tmp_path):
    from bot.journalisation import Journal

    (tmp_path / "trades.csv").write_text(
        "horodatage_utc;mode;actif;direction;montant;expiration_s;prix_entree;prix_sortie;resultat;pnl;solde_apres;strategie;commentaire\n"
        "ligne cassee;;;\n",
        encoding="utf-8",
    )
    relu = Journal(tmp_path)
    assert relu.trades == []


def test_appliquer_reglages_met_a_jour_le_risque(tmp_path):
    from bot.engine import Moteur

    moteur = Moteur(
        _settings(dossier_journal=tmp_path),
        journal=None,
    )
    nouveaux = moteur.reglages.mis_a_jour(
        {"taille_max_position": 12.0, "trades_max_par_jour": 7, "payout_pct": 90}
    )
    moteur.appliquer_reglages(nouveaux)

    assert moteur.risque.limites.montant_max_position == 12.0
    assert moteur.risque.limites.trades_max_par_jour == 7
    assert abs(moteur.execution.courtier.payout - 0.90) < 1e-9
    # L'instantané doit refléter les valeurs vivantes, pas celles du .env.
    assert moteur.instantane()["config"]["taille_max_position"] == 12.0
