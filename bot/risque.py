"""Gestion du risque : le dernier rempart avant l'exécution.

Toutes les limites sont vérifiées AVANT qu'un ordre ne soit transmis.
Un dépassement lève `RisqueRefuse`, que l'orchestrateur traite comme un
non-événement (aucun ordre, log d'avertissement, bot toujours vivant).

Limites implémentées :
    - montant maximal par position ;
    - fraction de capital risquée par trade ;
    - perte cumulée maximale sur la journée (reset à minuit UTC) ;
    - nombre maximal de trades par jour ;
    - interdiction de trader sans capital suffisant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class RisqueRefuse(Exception):
    """Le trade demandé viole une limite de risque."""


@dataclass(frozen=True)
class Limites:
    montant_max_position: float
    fraction_risque_par_trade: float
    perte_max_journaliere: float
    trades_max_par_jour: int


def calculer_budget(limites: Limites, capital: float) -> float:
    """Montant maximal autorisé pour un trade, arrondi au centime inférieur."""
    plafond_capital = max(0.0, capital) * limites.fraction_risque_par_trade
    return math.floor(min(limites.montant_max_position, plafond_capital) * 100) / 100


@dataclass
class EtatRisque:
    """Compteurs du jour, exposés au dashboard sous forme de jauges."""

    jour: str = ""
    trades_du_jour: int = 0
    pnl_du_jour: float = 0.0
    pertes_du_jour: float = 0.0
    gains_du_jour: float = 0.0
    series_perdantes: int = 0
    serie_perdante_max: int = 0

    def instantane(self, limites: Limites, capital: float) -> dict[str, Any]:
        perte_consommee = max(0.0, -min(self.pnl_du_jour, 0.0))
        return {
            "jour": self.jour,
            "trades_du_jour": self.trades_du_jour,
            "trades_max": limites.trades_max_par_jour,
            "pnl_du_jour": round(self.pnl_du_jour, 2),
            "pertes_du_jour": round(self.pertes_du_jour, 2),
            "gains_du_jour": round(self.gains_du_jour, 2),
            "perte_max_journaliere": limites.perte_max_journaliere,
            "perte_consommee": round(perte_consommee, 2),
            "perte_restante": round(max(0.0, limites.perte_max_journaliere - perte_consommee), 2),
            "pct_perte_utilisee": round(
                100 * perte_consommee / limites.perte_max_journaliere, 1
            )
            if limites.perte_max_journaliere
            else 0.0,
            "series_perdantes": self.series_perdantes,
            "serie_perdante_max": self.serie_perdante_max,
            "budget_trade": round(calculer_budget(limites, capital), 2),
        }


class GestionRisque:
    """Contrôle d'exposition avec réinitialisation quotidienne automatique."""

    def __init__(self, limites: Limites, capital_initial: float) -> None:
        self.limites = limites
        self.capital_initial = capital_initial
        self.etat = EtatRisque(jour=self._jour_utc())

    # ------------------------------------------------------------------ #
    @staticmethod
    def _jour_utc() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _rabattre_si_nouveau_jour(self) -> None:
        aujourdhui = self._jour_utc()
        if aujourdhui != self.etat.jour:
            self.etat = EtatRisque(jour=aujourdhui)

    def cloturer_journee(self) -> None:
        """Réinitialise les compteurs du jour.

        Utilisée à minuit UTC en exploitation réelle, et par les campagnes de
        test pour simuler plusieurs journées d'affilée sans attendre.
        """
        self.etat = EtatRisque(jour=self._jour_utc())

    def budget_trade(self, limites: Limites | None = None, capital: float | None = None) -> float:
        """Montant autorisé pour le prochain trade."""
        limites = limites or self.limites
        capital = self.capital_initial if capital is None else capital
        return calculer_budget(limites, capital)

    # ------------------------------------------------------------------ #
    def valider(self, montant: float, capital: float) -> None:
        """Vérifie toutes les limites. Lève RisqueRefuse au premier échec."""
        self._rabattre_si_nouveau_jour()
        limites = self.limites

        if montant <= 0:
            raise RisqueRefuse(f"Montant invalide : {montant:.2f} $.")

        if montant > limites.montant_max_position + 1e-9:
            raise RisqueRefuse(
                f"Montant {montant:.2f} $ > plafond de position "
                f"{limites.montant_max_position:.2f} $."
            )

        if montant > self.budget_trade(limites, capital) + 1e-9:
            raise RisqueRefuse(
                f"Montant {montant:.2f} $ > budget risque autorisé "
                f"({self.budget_trade(limites, capital):.2f} $ = "
                f"{limites.fraction_risque_par_trade:.1%} du capital)."
            )

        if montant > capital + 1e-9:
            raise RisqueRefuse("Capital insuffisant pour engager ce montant.")

        if self.etat.trades_du_jour >= limites.trades_max_par_jour:
            raise RisqueRefuse(
                f"Quota journalier atteint ({self.etat.trades_du_jour}/"
                f"{limites.trades_max_par_jour} trades)."
            )

        perte_actuelle = max(0.0, -self.etat.pnl_du_jour)
        if perte_actuelle + montant > limites.perte_max_journaliere + 1e-9:
            raise RisqueRefuse(
                f"Perte journalière maximale atteinte : "
                f"{perte_actuelle:.2f} $ perdus sur "
                f"{limites.perte_max_journaliere:.2f} $ autorisés."
            )

    # ------------------------------------------------------------------ #
    def enregistrer_trade(self, montant: float) -> None:
        """Comptabilise un trade AVANT sa résolution (protection anti-rafale)."""
        self._rabattre_si_nouveau_jour()
        self.etat.trades_du_jour += 1

    def enregistrer_resultat(self, pnl: float) -> None:
        """Met à jour le P&L du jour et les séries perdantes."""
        self._rabattre_si_nouveau_jour()
        self.etat.pnl_du_jour += pnl
        if pnl >= 0:
            self.etat.gains_du_jour += pnl
            self.etat.series_perdantes = 0
        else:
            self.etat.pertes_du_jour += abs(pnl)
            self.etat.series_perdantes += 1
            self.etat.serie_perdante_max = max(
                self.etat.serie_perdante_max, self.etat.series_perdantes
            )

    # ------------------------------------------------------------------ #
    def peut_trader(self, capital: float) -> tuple[bool, str]:
        """Version non bloquante, pratique pour l'UI."""
        try:
            self.valider(self.budget_trade(capital=capital) or 0.01, capital)
        except RisqueRefuse as exc:
            return False, str(exc)
        return True, "Toutes les limites sont respectées."
