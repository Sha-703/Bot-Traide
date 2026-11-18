"""Tests de la campagne : archivage du journal, remise à zéro, verrou.

Une campagne = un journal. Sans cette séparation, les statistiques mélangent
des flux, des mises et des réglages différents : elles ne mesurent plus rien.
"""

from __future__ import annotations

import asyncio

import pytest

from bot.config import Settings
from bot.engine import Moteur
from bot.journalisation import COLONNES, Journal, TradeJournalise
from bot.verrou import InstanceDejaOuverte, VerrouInstance


def _trade(pnl: float = 0.87, solde: float = 1000.87) -> TradeJournalise:
    return TradeJournalise(
        horodatage_utc="2026-11-17T21:00:00+00:00",
        mode="DEMO",
        actif="BTCUSDT",
        direction="ACHAT",
        montant=1.0,
        expiration_s=60,
        prix_entree=64500.0,
        prix_sortie=64520.0,
        resultat="GAGNANT" if pnl > 0 else "PERDANT",
        pnl=pnl,
        solde_apres=solde,
        strategie="EMA9/21",
        commentaire="test",
    )


# --------------------------------------------------------------------------- #
#  Archivage
# --------------------------------------------------------------------------- #
def test_archiver_deplace_le_fichier_et_vide_le_tampon(tmp_path):
    journal = Journal(tmp_path)
    journal.enregistrer(_trade())

    archive = journal.archiver_campagne()

    assert archive is not None
    assert archive.exists()
    assert archive.parent.name == "campagnes"
    assert journal.trades == []
    # Le journal courant est recréé avec son en-tête, prêt à écrire.
    entete = journal.chemin_trades.read_text(encoding="utf-8").strip().split(";")
    assert entete == COLONNES
    # L'archive contient bien le trade, pas l'en-tête seul.
    lignes = archive.read_text(encoding="utf-8").strip().splitlines()
    assert len(lignes) == 2


def test_archiver_un_journal_vide_ne_cree_rien(tmp_path):
    journal = Journal(tmp_path)
    assert journal.archiver_campagne() is None
    assert not (tmp_path / "campagnes").exists()


def test_archives_successives_ne_s_ecrasent_pas(tmp_path, monkeypatch):
    journal = Journal(tmp_path)
    journal.enregistrer(_trade())
    premier = journal.archiver_campagne()

    journal.enregistrer(_trade(pnl=-1.0))
    second = journal.archiver_campagne()

    assert premier and second and premier != second
    assert premier.exists() and second.exists()


# --------------------------------------------------------------------------- #
#  Remise à zéro du moteur
# --------------------------------------------------------------------------- #
def _settings(tmp_path, **extra) -> Settings:
    base = dict(
        mode_reel=False, capital_initial=1000.0, dossier_journal=tmp_path
    )
    base.update(extra)
    return Settings(**base)


def test_nouvelle_campagne_archive_et_remet_les_compteurs_a_zero(tmp_path):
    moteur = Moteur(_settings(tmp_path))
    moteur.journal.enregistrer(_trade(pnl=-2.0, solde=998.0))
    moteur.capital = 998.0
    moteur.risque.etat.trades_du_jour = 23
    moteur.risque.etat.pnl_du_jour = -9.17

    resultat = asyncio.run(moteur.nouvelle_campagne())

    assert resultat["archive"]
    assert resultat["capital"] == 1000.0
    assert moteur.capital == 1000.0
    assert moteur.risque.etat.trades_du_jour == 0
    assert moteur.risque.etat.pnl_du_jour == 0.0
    assert moteur.journal.trades == []
    assert moteur.statut.value == "ARRETE"


def test_reinitialiser_tout_restaure_les_reglages_du_env(tmp_path):
    # .env : flux Binance, mise 1 $, quota 20.
    moteur = Moteur(_settings(tmp_path, flux_prix="binance", montant_trade=1.0))
    # L'opérateur a tout changé à la main : c'est le cas « je veux repartir propre ».
    moteur.appliquer_reglages(
        moteur.reglages.mis_a_jour(
            {"montant_trade": 4.5, "trades_max_par_jour": 200, "flux_prix": "simule"}
        )
    )
    moteur.journal.enregistrer(_trade(pnl=-2.0, solde=998.0))
    moteur.capital = 998.0
    moteur.risque.etat.trades_du_jour = 42

    resultat = asyncio.run(moteur.reinitialiser_tout(None))

    assert resultat["archive"]
    assert moteur.capital == 1000.0
    assert moteur.risque.etat.trades_du_jour == 0
    assert moteur.reglages.montant_trade == 1.0
    assert moteur.reglages.trades_max_par_jour == 20
    assert moteur.reglages.flux_prix == "binance"  # valeur du .env
    assert moteur.source.nom == "binance"
    assert resultat["reglages"]["flux_prix"] == "binance"
    assert moteur.journal.trades == []
    assert moteur.statut.value == "ARRETE"


def test_journal_tolere_un_fichier_avec_bom(tmp_path):
    chemin = tmp_path / "trades.csv"
    with chemin.open("w", newline="", encoding="utf-8-sig") as f:
        import csv

        writer = csv.DictWriter(f, fieldnames=COLONNES, delimiter=";")
        writer.writeheader()
        writer.writerow(_trade().__dict__)

    journal = Journal(tmp_path)
    assert len(journal.trades) == 1
    assert journal.trades[0].actif == "BTCUSDT"


# --------------------------------------------------------------------------- #
#  Verrou mono-instance
# --------------------------------------------------------------------------- #
def test_verrou_refuse_une_seconde_instance(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.verrou.pid_vivant", lambda pid: True)
    (tmp_path / ".verrou").write_text("424242\n", encoding="utf-8")
    with pytest.raises(InstanceDejaOuverte):
        VerrouInstance(tmp_path).prendre()


def test_verrou_reprend_un_pid_mort(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.verrou.pid_vivant", lambda pid: False)
    (tmp_path / ".verrou").write_text("424242\n", encoding="utf-8")
    verrou = VerrouInstance(tmp_path)
    verrou.prendre()
    assert verrou.chemin.exists()


def test_verrou_libere_a_l_arret(tmp_path):
    verrou = VerrouInstance(tmp_path)
    verrou.prendre()
    assert verrou.chemin.exists()
    verrou.liberer()
    assert not verrou.chemin.exists()
