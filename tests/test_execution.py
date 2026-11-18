"""Tests du module d'exécution : le mode réel doit être impossible."""

from __future__ import annotations

import csv

import pytest

from bot.config import Settings
from bot.execution import (
    CourtierReel,
    ExecutionReelleBloquee,
    ExecutionRefusee,
    MoteurExecution,
)
from bot.journalisation import Journal
from bot.risque import GestionRisque, Limites
from bot.strategie import Direction, Signal


def _settings(**extra) -> Settings:
    base = dict(
        mode_reel=False,
        ssid=None,
        actif="EURUSD",
        capital_initial=1000.0,
        montant_trade=1.0,
        taille_max_position=5.0,
        fraction_risque_par_trade=0.01,
        perte_max_journaliere=30.0,
        trades_max_par_jour=20,
        payout=0.87,
        duree_expiration_s=60,
    )
    base.update(extra)
    return Settings(**base)


def _signal(direction=Direction.ACHAT, prix=1.0850, ts=1_000.0) -> Signal:
    return Signal(
        direction=direction,
        prix=prix,
        horodatage=ts,
        raison="test",
        ema_rapide=prix + 0.0001,
        ema_lente=prix,
        ecart_bps=1.0,
    )


@pytest.fixture()
def moteur(tmp_path):
    settings = _settings()
    risque = GestionRisque(
        Limites(5.0, 0.01, 30.0, 20), capital_initial=settings.capital_initial
    )
    journal = Journal(tmp_path)
    return MoteurExecution(settings, risque, journal), risque, journal


def test_mode_reel_leve_une_exception(moteur):
    execution, _, _ = moteur
    # Le verrou est un drapeau d'instance : seul le moteur peut le lever, et
    # la levée suffit à bloquer toute exécution.
    execution.mode_reel = True
    with pytest.raises(ExecutionReelleBloquee, match="réelle"):
        execution.executer(_signal(), capital=1000.0)


def test_courtier_reel_non_implemente():
    with pytest.raises(ExecutionReelleBloquee):
        CourtierReel(ssid="x")


def test_ouverture_puis_resolution_gagnante(moteur):
    execution, risque, journal = moteur
    ordre = execution.executer(_signal(), capital=1000.0)
    assert ordre.montant == 1.0
    assert execution.ordre_en_cours is not None

    _, resultat, capital = execution.resoudre(1.0860, capital=1000.0)
    assert resultat.resultat == "GAGNANT"
    assert resultat.pnl == pytest.approx(0.87)
    assert capital == pytest.approx(1000.87)
    assert journal.trades[0].resultat == "GAGNANT"
    assert execution.ordre_en_cours is None


def test_resolution_perdante(moteur):
    execution, _, journal = moteur
    execution.executer(_signal(direction=Direction.VENTE), capital=1000.0)
    _, resultat, capital = execution.resoudre(1.0860, capital=1000.0)
    assert resultat.resultat == "PERDANT"
    assert capital == pytest.approx(999.0)
    assert journal.statistiques()["perdants"] == 1


def test_egalite_remboursee(moteur):
    execution, _, _ = moteur
    execution.executer(_signal(), capital=1000.0)
    _, resultat, capital = execution.resoudre(1.0850, capital=1000.0)
    assert resultat.resultat == "REMBOURSE"
    assert capital == pytest.approx(1000.0)


def test_un_seul_ordre_a_la_fois(moteur):
    execution, _, _ = moteur
    execution.executer(_signal(), capital=1000.0)
    with pytest.raises(ExecutionRefusee, match="déjà en cours"):
        execution.executer(_signal(), capital=1000.0)


def test_signal_neutre_refuse(moteur):
    execution, _, _ = moteur
    with pytest.raises(ExecutionRefusee, match="neutre"):
        execution.executer(_signal(direction=Direction.NEUTRE), capital=1000.0)


def test_journal_csv_contient_l_entete_et_la_ligne(moteur):
    execution, _, journal = moteur
    execution.executer(_signal(), capital=1000.0)
    execution.resoudre(1.0860, capital=1000.0)
    with journal.chemin_trades.open(encoding="utf-8") as f:
        lignes = list(csv.reader(f, delimiter=";"))
    assert lignes[0][0] == "horodatage_utc"
    assert lignes[1][3] == "ACHAT"
    assert lignes[1][8] == "GAGNANT"


def test_le_ssid_n_est_jamais_journalise(moteur):
    execution, _, journal = moteur
    execution.executer(_signal(), capital=1000.0)
    execution.resoudre(1.0860, capital=1000.0)
    contenu = journal.chemin_trades.read_text(encoding="utf-8")
    assert "ssid" not in contenu.lower()
    assert "jeton" not in contenu.lower()
