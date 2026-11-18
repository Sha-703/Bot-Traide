"""Tests de la stratégie EMA : pas de signal fantôme, pas de look-ahead."""

from __future__ import annotations

from bot.strategie import Direction, StrategieMoyennesMobiles


def _nourrir(strategie: StrategieMoyennesMobiles, prix: list[float], t0: float = 1_000.0):
    signaux = []
    for i, p in enumerate(prix):
        s = strategie.maj(p, t0 + i)
        if s is not None:
            signaux.append(s)
    return signaux


def test_aucun_signal_pendant_la_chauffe():
    strategie = StrategieMoyennesMobiles(ema_rapide=3, ema_lente=5)
    signaux = _nourrir(strategie, [1.0 + i * 0.001 for i in range(9)])
    assert signaux == []


def test_croisement_haussier_genere_un_achat():
    strategie = StrategieMoyennesMobiles(ema_rapide=3, ema_lente=6, seuil_ecart_bps=0.5, cooldown_secondes=0)
    # Descente puis remontée franche : croisement par le bas.
    serie = [1.10 - i * 0.001 for i in range(14)] + [1.086 + i * 0.003 for i in range(14)]
    signaux = _nourrir(strategie, serie)
    assert signaux, "un croisement haussier doit produire un signal"
    assert signaux[0].direction is Direction.ACHAT
    assert "Croisement haussier" in signaux[0].raison


def test_croisement_baissier_genere_une_vente():
    strategie = StrategieMoyennesMobiles(ema_rapide=3, ema_lente=6, seuil_ecart_bps=0.5, cooldown_secondes=0)
    serie = [1.08 + i * 0.001 for i in range(14)] + [1.094 - i * 0.003 for i in range(14)]
    signaux = _nourrir(strategie, serie)
    assert signaux
    assert signaux[0].direction is Direction.VENTE


def test_marche_parfaitement_plat_ne_genere_rien():
    strategie = StrategieMoyennesMobiles(ema_rapide=3, ema_lente=8)
    signaux = _nourrir(strategie, [1.0850] * 60)
    assert signaux == []


def test_cooldown_bloque_les_rafales():
    strategie = StrategieMoyennesMobiles(ema_rapide=3, ema_lente=6, seuil_ecart_bps=0.1, cooldown_secondes=3600)
    # Deux croisements rapprochés dans le même sens.
    serie = (
        [1.10 - i * 0.002 for i in range(12)]
        + [1.078 + i * 0.004 for i in range(12)]  # ACHAT
        + [1.126 - i * 0.004 for i in range(12)]
        + [1.078 + i * 0.004 for i in range(12)]  # ACHAT à nouveau
    )
    signaux = _nourrir(strategie, serie)
    achats = [s for s in signaux if s.direction is Direction.ACHAT]
    assert len(achats) == 1, "le cooldown doit empêcher le second achat"


def test_etat_expose_pour_le_dashboard():
    strategie = StrategieMoyennesMobiles(ema_rapide=3, ema_lente=6)
    _nourrir(strategie, [1.08 + i * 0.0005 for i in range(30)])
    etat = strategie.etat
    assert etat.dernier_prix is not None
    assert etat.ema_rapide is not None and etat.ema_lente is not None
    assert len(etat.historique_prix) > 0
    instantane = etat.instantane()
    assert set(instantane) >= {"prix", "ema_r", "ema_l", "tendance"}
