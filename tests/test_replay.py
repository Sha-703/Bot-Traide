"""Tests du moteur de rejeu : logique d'ouverture/clôture et comptabilité."""

from __future__ import annotations

import pytest

from bot.replay import EtatEcheance, rejouer
from bot.strategie import Direction


def serie_retour(debut: int = 1000):
    """Forte baisse puis forte hausse : garantit au moins un croisement."""
    p = 100.0
    ticks = []
    for _ in range(60):
        p -= 0.8 / 60
        ticks.append((float(len(ticks) + debut), round(p, 6)))
    for _ in range(80):
        p += 3.0 / 80
        ticks.append((float(len(ticks) + debut), round(p, 6)))
    return ticks


def test_long_expiry_ouvre_au_plus_un_trade():
    # Échéance énorme + cooldown nul => une seule position possible.
    etats = rejouer(serie_retour(), [10_000], cooldown_s=0)
    assert etats[10_000]["trades"] <= 1


def test_la_strategie_emet_au_moins_un_signal():
    etats = rejouer(serie_retour(), [30], cooldown_s=0)
    assert etats[30]["trades"] >= 1


def test_comptabilite_coherente():
    etats = rejouer(serie_retour(), [5, 30], cooldown_s=5)
    for exp, s in etats.items():
        assert s["trades"] == s["gagnants"] + s["perdants"] + s["nuls"]
        attendu = round(s["gagnants"] * 0.87 - s["perdants"] * 1.0, 6)
        assert round(s["pnl"], 6) == pytest.approx(attendu, abs=1e-6)
        assert s["serie_max"] <= s["trades"]
        assert s["dd_max"] >= 0.0
        assert s["ticks"] == len(serie_retour())


def test_serie_plate_sans_croisement_ne_trade_pas():
    ticks = [(float(i), 100.0) for i in range(200)]
    etats = rejouer(ticks, [30], cooldown_s=0)
    assert etats[30]["trades"] == 0


def test_deux_expiries_memes_ticks():
    ticks = serie_retour()
    etats = rejouer(ticks, [1, 300], cooldown_s=0)
    assert etats[1]["ticks"] == etats[300]["ticks"] == len(ticks)


def test_prix_invalide_propage_erreur():
    etat = EtatEcheance(30)
    with pytest.raises(ValueError):
        etat.tick(1000.0, -1.0)
