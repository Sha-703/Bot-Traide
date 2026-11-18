"""Stratégie de trading : croisement de moyennes mobiles exponentielles (EMA).

Principe
--------
    EMA_rapide croise EMA_lente vers le haut  -> signal ACHAT  (call)
    EMA_rapide croise EMA_lente vers le bas   -> signal VENTE  (put)

Filtres anti-bruit intégrés :
    1. Période de chauffe : aucun signal avant d'avoir assez de points.
    2. Écart minimal (en points de base) entre les deux EMA : évite les
       croisements « collés » typiques d'un marché en range.
    3. Cooldown global : deux signaux consécutifs doivent être espacés d'un
       délai minimal, quelle que soit leur direction (anti-whipsaw).

Aucune promesse de rentabilité : une stratégie de suivi de tendance perd de
l'argent par construction dans un marché sans tendance.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Direction(str, Enum):
    """Direction d'un trade binaire."""

    ACHAT = "ACHAT"
    VENTE = "VENTE"
    NEUTRE = "NEUTRE"


@dataclass(frozen=True)
class Signal:
    """Signal émis par la stratégie, avec sa justification auditable."""

    direction: Direction
    prix: float
    horodatage: float
    raison: str
    ema_rapide: float
    ema_lente: float
    ecart_bps: float

    @property
    def exploitable(self) -> bool:
        return self.direction is not Direction.NEUTRE


@dataclass
class EtatStrategie:
    """Vue temps réel exposée au dashboard (aucune logique métier ici)."""

    dernier_prix: float | None = None
    ema_rapide: float | None = None
    ema_lente: float | None = None
    ecart_bps: float = 0.0
    tendance: str = "indéterminée"
    points_chauffe: int = 0
    points_requis: int = 0
    historique_prix: deque[float] = field(default_factory=lambda: deque(maxlen=240))
    historique_rapide: deque[float] = field(default_factory=lambda: deque(maxlen=240))
    historique_lente: deque[float] = field(default_factory=lambda: deque(maxlen=240))

    def instantane(self) -> dict[str, Any]:
        return {
            "dernier_prix": self.dernier_prix,
            "ema_rapide": self.ema_rapide,
            "ema_lente": self.ema_lente,
            "ecart_bps": round(self.ecart_bps, 3),
            "tendance": self.tendance,
            "points_chauffe": self.points_chauffe,
            "points_requis": self.points_requis,
            "prix": list(self.historique_prix),
            "ema_r": [round(v, 6) for v in self.historique_rapide],
            "ema_l": [round(v, 6) for v in self.historique_lente],
        }


class StrategieMoyennesMobiles:
    """Croisement d'EMA avec filtres de bruit et de cooldown.

    L'EMA est mise à jour de façon incrémentale (O(1) par tick) :
        EMA_t = alpha * prix_t + (1 - alpha) * EMA_{t-1}
        alpha = 2 / (periode + 1)
    """

    def __init__(
        self,
        ema_rapide: int = 9,
        ema_lente: int = 21,
        seuil_ecart_bps: float = 1.0,
        cooldown_secondes: int = 30,
        max_attente_ticks: int = 40,
    ) -> None:
        if ema_rapide >= ema_lente:
            raise ValueError("La période rapide doit être inférieure à la période lente.")
        self.ema_rapide = ema_rapide
        self.ema_lente = ema_lente
        self.seuil_ecart_bps = seuil_ecart_bps
        self.cooldown_secondes = cooldown_secondes
        self.max_attente_ticks = max_attente_ticks

        self._alpha_rapide = 2 / (ema_rapide + 1)
        self._alpha_lente = 2 / (ema_lente + 1)

        self._ema_r: float | None = None
        self._ema_l: float | None = None
        self._signe_precedent = 0
        # -inf : le tout premier signal n'est jamais bloqué par le cooldown.
        self._dernier_signal_ts: float = float("-inf")
        self._derniere_direction: Direction = Direction.NEUTRE
        self._attente: Direction | None = None
        self._ticks_attente = 0
        self._nb_points = 0

        self.etat = EtatStrategie(
            points_requis=ema_lente * 2,  # marge pour stabiliser l'EMA lente
        )

    # ------------------------------------------------------------------ #
    def maj(self, prix: float, horodatage: float) -> Signal | None:
        """Ingère un prix et retourne un Signal si un croisement valide survient."""
        if prix <= 0:
            raise ValueError(f"Prix invalide : {prix}")

        self._nb_points += 1
        self._ema_r = (
            prix if self._ema_r is None else self._alpha_rapide * prix + (1 - self._alpha_rapide) * self._ema_r
        )
        self._ema_l = (
            prix if self._ema_l is None else self._alpha_lente * prix + (1 - self._alpha_lente) * self._ema_l
        )

        ecart_bps = (self._ema_r - self._ema_l) / self._ema_l * 10_000 if self._ema_l else 0.0
        signe = 1 if ecart_bps > 0 else (-1 if ecart_bps < 0 else 0)

        # Mise à jour de l'état public (dashboard).
        self.etat.dernier_prix = prix
        self.etat.ema_rapide = self._ema_r
        self.etat.ema_lente = self._ema_l
        self.etat.ecart_bps = ecart_bps
        self.etat.points_chauffe = min(self._nb_points, self.etat.points_requis)
        self.etat.tendance = (
            "haussière" if signe > 0 else ("baissière" if signe < 0 else "plate")
        )
        self.etat.historique_prix.append(prix)
        self.etat.historique_rapide.append(self._ema_r)
        self.etat.historique_lente.append(self._ema_l)

        # 1) Chauffe terminée ?
        if self._nb_points < self.etat.points_requis or signe == 0:
            self._signe_precedent = signe or self._signe_precedent
            return None

        # 2) Croisement détecté ? On arme une attente de confirmation.
        #    Un signal est émis quelques ticks APRÈS le croisement, quand
        #    l'écart entre les EMA dépasse réellement le seuil : au moment
        #    exact du croisement l'écart vaut zéro, il ne peut donc jamais
        #    servir de filtre.
        croisement = self._signe_precedent != 0 and signe != self._signe_precedent
        self._signe_precedent = signe

        if croisement:
            self._attente = Direction.ACHAT if signe > 0 else Direction.VENTE
            self._ticks_attente = 0
            return None

        if self._attente is None or signe == 0:
            return None

        # 3) Confirmation : le mouvement doit se poursuivre dans le bon sens.
        self._ticks_attente += 1
        sens_attendu = 1 if self._attente is Direction.ACHAT else -1
        if signe != sens_attendu:
            self._attente = None  # faux croisement : on abandonne
            return None
        if abs(ecart_bps) < self.seuil_ecart_bps:
            if self._ticks_attente > self.max_attente_ticks:
                self._attente = None  # confirmation trop lente : on abandonne
            return None

        direction = self._attente
        self._attente = None

        # 4) Cooldown global : on interdit deux signaux rapprochés, même de
        #    sens opposés. C'est un frein anti-whipsaw : en marché nerveux, un
        #    croisement suivi d'un contre-croisement est le pire scénario.
        if horodatage - self._dernier_signal_ts < self.cooldown_secondes:
            return None

        self._dernier_signal_ts = horodatage
        self._derniere_direction = direction

        return Signal(
            direction=direction,
            prix=prix,
            horodatage=horodatage,
            raison=(
                f"Croisement {'haussier' if direction is Direction.ACHAT else 'baissier'} "
                f"EMA{self.ema_rapide}/EMA{self.ema_lente} confirmé "
                f"(écart {ecart_bps:.2f} bps, {self._ticks_attente} tick(s))"
            ),
            ema_rapide=self._ema_r,
            ema_lente=self._ema_l,
            ecart_bps=ecart_bps,
        )

    # ------------------------------------------------------------------ #
    def reinitialiser(self) -> None:
        """Remet la stratégie à zéro (nouvelle session, changement d'actif)."""
        self.__init__(  # type: ignore[misc]
            ema_rapide=self.ema_rapide,
            ema_lente=self.ema_lente,
            seuil_ecart_bps=self.seuil_ecart_bps,
            cooldown_secondes=self.cooldown_secondes,
        )
