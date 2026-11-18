"""Règles candidates pour le sweep de backtest.

Chaque règle suit la même interface que `StrategieMoyennesMobiles` :
`maj(prix, horodatage) -> Signal | None`, avec cooldown global anti-whipsaw
et une seule émission par « entrée de zone ». Aucun de ces signaux n'est
destiné à la production sans validation hors échantillon.
"""

from __future__ import annotations

import math
from collections import deque

from .strategie import Direction, Signal


def _signal(direction: Direction, prix: float, horodatage: float,
            raison: str, indicateur: float, ecart_bps: float) -> Signal:
    return Signal(
        direction=direction,
        prix=prix,
        horodatage=horodatage,
        raison=raison,
        ema_rapide=indicateur,
        ema_lente=indicateur,
        ecart_bps=ecart_bps,
    )


class Regle:
    """Base : compteur de chauffe + cooldown global."""

    def __init__(self, cooldown_s: int = 30, min_points: int = 0):
        self.cooldown_s = float(cooldown_s)
        self.min_points = int(min_points)
        self._points = 0
        self._dernier_signal_ts = float("-inf")

    def chauffe(self) -> bool:
        return self._points >= self.min_points

    def cooldown_ok(self, ts: float) -> bool:
        """Applique le cooldown global ; met à jour le timestamp si signal."""
        if ts - self._dernier_signal_ts < self.cooldown_s:
            return False
        self._dernier_signal_ts = ts
        return True

    def maj(self, prix: float, horodatage: float) -> Signal | None:
        if prix <= 0:
            raise ValueError(f"Prix invalide : {prix}")
        self._points += 1
        return self._maj(prix, horodatage)


class RetourRsi(Regle):
    """RSI > surachat => VENTE ; RSI < survente => ACHAT (retour à la moyenne)."""

    def __init__(
        self,
        periode: int = 14,
        survente: float = 30.0,
        surachat: float = 70.0,
        cooldown_s: int = 30,
    ) -> None:
        super().__init__(cooldown_s=cooldown_s, min_points=periode + 1)
        self.periode = periode
        self.survente = survente
        self.surachat = surachat
        self.closes: deque[float] = deque(maxlen=periode + 1)
        self._zone = 0  # -1 surachat, +1 survente, 0 neutre

    def _rsi(self, closes: deque[float]) -> float | None:
        if len(closes) < self.periode + 1:
            return None
        valeurs = list(closes)
        deltas = [valeurs[i] - valeurs[i - 1] for i in range(1, len(valeurs))]
        gains = [max(d, 0.0) for d in deltas[-self.periode:]]
        pertes = [max(-d, 0.0) for d in deltas[-self.periode:]]
        moy_gain = sum(gains) / self.periode
        moy_perte = sum(pertes) / self.periode
        if moy_perte == 0.0:
            return 100.0 if moy_gain > 0 else 50.0
        rs = moy_gain / moy_perte
        return 100.0 - 100.0 / (1.0 + rs)

    def _maj(self, prix: float, horodatage: float):
        self.closes.append(prix)
        rsi = self._rsi(self.closes)
        if rsi is None or not self.chauffe():
            return None
        nouvelle_zone = -1 if rsi > self.surachat else (1 if rsi < self.survente else 0)
        if nouvelle_zone == self._zone:
            return None
        direction = Direction.NEUTRE
        if nouvelle_zone == 1:
            direction = Direction.ACHAT
        elif nouvelle_zone == -1:
            direction = Direction.VENTE
        self._zone = nouvelle_zone
        if direction is Direction.NEUTRE or not self.cooldown_ok(horodatage):
            return None
        raison = (
            f"RSI({self.periode}) = {rsi:.1f} "
            f"({'survente' if direction is Direction.ACHAT else 'surachat'})"
        )
        return _signal(direction, prix, horodatage, raison, rsi, abs(rsi - 50.0))


class BollingerFade(Regle):
    """Prix hors bande => retour vers la moyenne (Bollinger)."""

    def __init__(
        self,
        periode: int = 20,
        k: float = 2.0,
        cooldown_s: int = 30,
    ) -> None:
        super().__init__(cooldown_s=cooldown_s, min_points=periode)
        self.periode = periode
        self.k = k
        self.closes: deque[float] = deque(maxlen=periode)
        self._hors_bande = False

    def _maj(self, prix: float, horodatage: float):
        self.closes.append(prix)
        if len(self.closes) < self.periode or not self.chauffe():
            return None
        data = list(self.closes)
        moyenne = sum(data) / self.periode
        variance = sum((x - moyenne) ** 2 for x in data) / self.periode
        ecart_type = math.sqrt(variance)
        if ecart_type <= 0.0:
            return None
        basse, haute = moyenne - self.k * ecart_type, moyenne + self.k * ecart_type
        direction = Direction.NEUTRE
        if prix < basse:
            direction = Direction.ACHAT
        elif prix > haute:
            direction = Direction.VENTE
        if direction is Direction.NEUTRE:
            self._hors_bande = False
            return None
        if self._hors_bande:
            return None
        self._hors_bande = True
        if not self.cooldown_ok(horodatage):
            return None
        raison = (
            f"Prix {'sous la bande basse' if direction is Direction.ACHAT else 'au-dessus de la bande haute'} "
            f"(Bollinger {self.periode}, k={self.k})"
        )
        return _signal(direction, prix, horodatage, raison, moyenne,
                       abs(prix - moyenne) / moyenne * 10_000)


class BreakoutDonchian(Regle):
    """Nouveau plus haut/plus bas sur N ticks => cassure."""

    def __init__(
        self,
        periode: int = 20,
        cooldown_s: int = 30,
    ) -> None:
        super().__init__(cooldown_s=cooldown_s, min_points=periode)
        self.periode = periode
        self.closes: deque[float] = deque(maxlen=periode)
        self._arme = True

    def _maj(self, prix: float, horodatage: float):
        if len(self.closes) < self.periode or not self.chauffe():
            self.closes.append(prix)
            return None
        historique = list(self.closes)
        self.closes.append(prix)
        max_precedent = max(historique)
        min_precedent = min(historique)
        if min_precedent < prix < max_precedent:
            self._arme = True
            return None
        if not self._arme:
            return None
        direction = Direction.ACHAT if prix >= max_precedent else Direction.VENTE
        if not self.cooldown_ok(horodatage):
            return None
        self._arme = False
        raison = (
            f"Cassure {'haussière' if direction is Direction.ACHAT else 'baissière'} "
            f"du plus {'haut' if direction is Direction.ACHAT else 'bas'} "
            f"sur {self.periode} ticks"
        )
        return _signal(direction, prix, horodatage, raison, prix, 0.0)
