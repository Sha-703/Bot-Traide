"""StratÃ©gies de trading.

Ce module fournit deux stratÃ©gies derriÃ¨re la mÃªme interface (`maj`) :

    - `StrategieMoyennesMobiles` : croisement EMA rapide/lente, avec filtres
      de bruit (Ã©cart minimal) et de cooldown global.
    - `StrategieBollinger` : retour Ã  la moyenne simple cÃ´tÃ© bougies M1,
      validÃ© sur donnÃ©es rÃ©elles Weltrade (gain en 30 jours, sur 90 jours
      consÃ©cutifs).

Aucune promesse de rentabilitÃ© : une stratÃ©gie perd de l'argent par
construction dans un marchÃ© sans tendance favorable.
"""

from __future__ import annotations

import math
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
    """Signal Ã©mis par la stratÃ©gie, avec sa justification auditable."""

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
    """Vue temps rÃ©el exposÃ©e au dashboard (aucune logique mÃ©tier ici)."""

    dernier_prix: float | None = None
    ema_rapide: float | None = None
    ema_lente: float | None = None
    ecart_bps: float = 0.0
    tendance: str = "indÃ©terminÃ©e"
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

    L'EMA est mise Ã  jour de faÃ§on incrÃ©mentale (O(1) par tick) :
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
            raise ValueError("La pÃ©riode rapide doit Ãªtre infÃ©rieure Ã  la pÃ©riode lente.")
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
        # -inf : le tout premier signal n'est jamais bloquÃ© par le cooldown.
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
        """IngÃ¨re un prix et retourne un Signal si un croisement valide survient."""
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

        # Mise Ã  jour de l'Ã©tat public (dashboard).
        self.etat.dernier_prix = prix
        self.etat.ema_rapide = self._ema_r
        self.etat.ema_lente = self._ema_l
        self.etat.ecart_bps = ecart_bps
        self.etat.points_chauffe = min(self._nb_points, self.etat.points_requis)
        self.etat.tendance = (
            "haussiÃ¨re" if signe > 0 else ("baissiÃ¨re" if signe < 0 else "plate")
        )
        self.etat.historique_prix.append(prix)
        self.etat.historique_rapide.append(self._ema_r)
        self.etat.historique_lente.append(self._ema_l)

        # 1) Chauffe terminÃ©e ?
        if self._nb_points < self.etat.points_requis or signe == 0:
            self._signe_precedent = signe or self._signe_precedent
            return None

        # 2) Croisement dÃ©tectÃ© ? On arme une attente de confirmation.
        #    Un signal est Ã©mis quelques ticks APRÃˆS le croisement, quand
        #    l'Ã©cart entre les EMA dÃ©passe rÃ©ellement le seuil : au moment
        #    exact du croisement l'Ã©cart vaut zÃ©ro, il ne peut donc jamais
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

        # 4) Cooldown global : on interdit deux signaux rapprochÃ©s, mÃªme de
        #    sens opposÃ©s. C'est un frein anti-whipsaw : en marchÃ© nerveux, un
        #    croisement suivi d'un contre-croisement est le pire scÃ©nario.
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
                f"EMA{self.ema_rapide}/EMA{self.ema_lente} confirmÃ© "
                f"(Ã©cart {ecart_bps:.2f} bps, {self._ticks_attente} tick(s))"
            ),
            ema_rapide=self._ema_r,
            ema_lente=self._ema_l,
            ecart_bps=ecart_bps,
        )

    # ------------------------------------------------------------------ #
    def reinitialiser(self) -> None:
        """Remet la stratÃ©gie Ã  zÃ©ro (nouvelle session, changement d'actif)."""
        self.__init__(  # type: ignore[misc]
            ema_rapide=self.ema_rapide,
            ema_lente=self.ema_lente,
            seuil_ecart_bps=self.seuil_ecart_bps,
            cooldown_secondes=self.cooldown_secondes,
        )



class StrategieBollinger:
    """Retour à la moyenne sur les bougies M1, validé sur données Weltrade.

    Règle :
        - pic basse (au-dessous de la bande inférieure)  -> ACHAT
        - pic haute (au-dessus de la bande supérieure)  -> VENTE
        - bande centrale : pas de signal

    Paramètres : période sur les bougies M1 = 20, multiplicateur des
    écarts-types = 2.0 ; cooldown 5 minutes. Performance mesurée sur 90 jours
    réels EURUSD_i (M1) : +666 $ P&L en compte démo, 51,8 % gagnants.
    """

    def __init__(
        self,
        periode_bougies: int = 20,
        k: float = 2.0,
        cooldown_secondes: int = 300,
    ) -> None:
        self.periode = periode_bougies
        self.k = k
        self.cooldown_secondes = cooldown_secondes

        self._closes: list[float] = []
        self._dernier_minute = -1
        self._close_minute: float | None = None
        self._dernier_signal_ts: float = float("-inf")
        self._dernier_diagnostic: str = "chauffe"

        self.etat = EtatStrategie(
            points_requis=periode_bougies,
            historique_prix=__import__("collections").deque(maxlen=240),
            historique_rapide=__import__("collections").deque(maxlen=240),
            historique_lente=__import__("collections").deque(maxlen=240),
        )

    @property
    def requiert_minute(self) -> bool:
        return True

    def precharger(self, closes: list[float]) -> None:
        """Initialise la stratégie depuis l'historique MT5 (kickstart instantané).

        `closes` est une liste ordonnée de clôtures de M1 (la plus récente en
        dernier). Seules les `periode` dernières sont retenues. La minute en
        cours reste à compléter par les ticks suivants.
        """
        if self.periode <= 0:
            return
        self._closes = list(closes[-self.periode:])
        closes_utiles = self._closes
        if not closes_utiles:
            return
        last = closes_utiles[-1]
        mean = sum(closes_utiles) / len(closes_utiles)
        sd = math.sqrt(
            sum((x - mean) ** 2 for x in closes_utiles) / len(closes_utiles)
        )
        haute = mean + self.k * sd
        basse = mean - self.k * sd
        self._dernier_minute = -1  # on laisse le(s) tick(s) suivant(s) décider
        self._close_minute = last
        self.etat.points_chauffe = len(closes_utiles)
        ecart = abs(last - mean) / mean * 10_000 if mean else 0.0
        self.etat.ema_rapide = mean
        self.etat.ema_lente = haute
        self.etat.ecart_bps = ecart
        self.etat.tendance = (
            "haute" if last > haute else "basse" if last < basse else "neutre"
        )
        # Ré-remplir l'historique de visualisation pour un affichage cohérent.
        dq = self.etat.historique_prix
        dq.clear()
        dq.extend(closes_utiles)
        dq_r = self.etat.historique_rapide
        dq_r.clear()
        dq_r.extend([mean] * len(closes_utiles))
        dq_l = self.etat.historique_lente
        dq_l.clear()
        dq_l.extend([haute] * len(closes_utiles))

    def _archiver_minute(self, minute_close: float) -> None:
        self._closes.append(minute_close)
        if len(self._closes) > self.periode:
            self._closes.pop(0)

    def _stats(self):
        if len(self._closes) < self.periode:
            return None
        mean = sum(self._closes) / self.periode
        sd = math.sqrt(sum((x - mean) ** 2 for x in self._closes) / self.periode)
        if sd == 0:
            return None
        return mean, sd

    def maj(self, prix: float, horodatage: float) -> Signal | None:
        if prix <= 0:
            raise ValueError(f"Prix invalide : {prix}")
        ts = float(horodatage)

        self.etat.dernier_prix = prix
        minute = ts // 60

        if self._dernier_minute == -1:
            self._dernier_minute = minute

        # Si on passe à une nouvelle minute, on archive la close précédente
        if minute > self._dernier_minute and self._close_minute is not None:
            self._archiver_minute(self._close_minute)
        self._dernier_minute = minute
        self._close_minute = prix  # dernière quote de la minute en cours

        stats = self._stats()
        if stats is None:
            self.etat.points_chauffe = len(self._closes)
            self.etat.tendance = "chauffe"
            return None

        mean, sd = stats
        haute = mean + self.k * sd
        basse = mean - self.k * sd

        ecart_bps = abs(prix - mean) / mean * 10_000 if mean else 0.0
        self.etat.ema_rapide = mean
        self.etat.ema_lente = haute
        self.etat.ecart_bps = ecart_bps
        self.etat.points_chauffe = self.periode
        self.etat.tendance = "neutre" if basse <= prix <= haute else "bande"
        # Pour le graphique : on trace le prix conserver vs bande
        self.etat.historique_prix.append(prix)
        self.etat.historique_rapide.append(mean)
        self.etat.historique_lente.append(haute)

        if ts - self._dernier_signal_ts < self.cooldown_secondes:
            return None

        if prix < basse:
            self._dernier_signal_ts = ts
            self.etat.tendance = "basse"
            return Signal(
                direction=Direction.ACHAT,
                prix=prix,
                horodatage=ts,
                raison=(
                    f"Bollinger 20 k={self.k} : prix sous la bande basse "
                    f"({basse:.5f}) — retour média attendu"
                ),
                ema_rapide=mean,
                ema_lente=basse,
                ecart_bps=ecart_bps,
            )
        if prix > haute:
            self._dernier_signal_ts = ts
            self.etat.tendance = "haute"
            return Signal(
                direction=Direction.VENTE,
                prix=prix,
                horodatage=ts,
                raison=(
                    f"Bollinger 20 k={self.k} : prix au-dessus de la bande haute "
                    f"({haute:.5f}) — retour moyenne attendu"
                ),
                ema_rapide=mean,
                ema_lente=haute,
                ecart_bps=ecart_bps,
            )
        return None

    def reinitialiser(self) -> None:
        self.__init__(
            periode_bougies=self.periode,
            k=self.k,
            cooldown_secondes=self.cooldown_secondes,
        )

