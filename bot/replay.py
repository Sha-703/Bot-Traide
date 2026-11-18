"""Rejeu d'une stratégie sur une série de ticks historiques.

Ce module n'a aucune dépendance réseau : il est le cœur du backtest et il
est testable comme le reste du bot. Une seule échéance à la fois, une seule
position ouverte à la fois, cooldown identique au live — le rejeu ne « triche »
en rien.

Aucune interpolation entre ticks : un trade à 60 s se résout sur la clôture du
tick le plus proche de l'échéance (modèle « close vers close »). C'est le même
approximation que fait la console en paper.
"""

from __future__ import annotations

from typing import Iterable, Iterator

from .strategie import Direction, StrategieMoyennesMobiles

EST_ETATS = (
    "ticks", "trades", "gagnants", "perdants", "nuls",
    "pnl", "gains_bruts", "pertes_bruts", "dd_max", "serie_max",
)


def stats_vides() -> dict:
    """Compatibilité avec l'ancien nom de la fonction interne au script."""
    return {cle: 0.0 for cle in EST_ETATS}


class EtatEcheance:
    """Rejeu d'UNE échéance sur un flux continu de ticks."""

    def __init__(
        self,
        expiration_s: int,
        ema_rapide: int = 9,
        ema_lente: int = 21,
        seuil_ecart_bps: float = 1.0,
        cooldown_s: int = 30,
        payout: float = 0.87,
        mise: float = 1.0,
        strategie: object | None = None,
    ) -> None:
        if expiration_s < 1:
            raise ValueError("L'échéance doit être d'au moins 1 seconde.")
        if not 0 < payout <= 1:
            raise ValueError("Le payout doit être compris entre 0 exclu et 1.")
        self.expiration_s = expiration_s
        self.strategie = strategie or StrategieMoyennesMobiles(
            ema_rapide=ema_rapide,
            ema_lente=ema_lente,
            seuil_ecart_bps=seuil_ecart_bps,
            cooldown_secondes=cooldown_s,
        )
        self.payout = payout
        self.mise = mise
        self.trade: dict | None = None
        self.sommet = 0.0
        self.equity = 0.0
        self.serie = 0
        self.stats = stats_vides()

    # ------------------------------------------------------------------ #
    def tick(self, horodatage: float, close: float) -> None:
        s = self.stats
        s["ticks"] += 1

        # 1) Résolution d'un trade arrivé à échéance (un seul à la fois).
        if self.trade is not None and horodatage >= self.trade["echeance"]:
            sortie = close
            if abs(sortie - self.trade["entree"]) < 1e-12:
                pnl = 0.0
                s["nuls"] += 1
            else:
                gagne = (
                    sortie > self.trade["entree"]
                    if self.trade["direction"] == Direction.ACHAT
                    else sortie < self.trade["entree"]
                )
                if gagne:
                    pnl = round(self.mise * self.payout, 6)
                    s["gagnants"] += 1
                    s["gains_bruts"] += pnl
                else:
                    pnl = -self.mise
                    s["perdants"] += 1
                    s["pertes_bruts"] += self.mise
            s["pnl"] += pnl
            s["trades"] += 1
            self.equity += pnl
            self.sommet = max(self.sommet, self.equity)
            s["dd_max"] = max(s["dd_max"], self.sommet - self.equity)
            self.serie = self.serie + 1 if pnl < 0 else 0
            s["serie_max"] = max(s["serie_max"], self.serie)
            self.trade = None

        # 2) La stratégie est nourrie à chaque tick, même trade ouvert.
        signal = self.strategie.maj(close, horodatage)

        # 3) Un seul trade à la fois.
        if (
            self.trade is None
            and signal is not None
            and signal.exploitable
        ):
            self.trade = {
                "direction": signal.direction,
                "entree": close,
                "echeance": horodatage + self.expiration_s,
            }


def rejouer(
    ticks: Iterable[tuple[float, float]],
    expiries: Iterable[int],
    **parametres,
) -> dict[int, dict]:
    """Joue le même flux sur plusieurs échéances en UNE seule passe."""
    etats = {e: EtatEcheance(e, **parametres) for e in expiries}
    for ts, close in ticks:
        for e in etats:
            etats[e].tick(ts, close)
    return {e: dict(st.stats) for e, st in etats.items()}
