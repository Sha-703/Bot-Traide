"""Réglages modifiables à chaud depuis la console.

Tout ce qui est « métier » dans l'interface (mise, plafonds, garde-fous,
expiration, payout) vit ici et **reste en mémoire** : les valeurs du `.env`
servent de point de départ, la console permet de les ajuster sans redémarrer.

Chaque mise à jour est validée. Les bornes ne sont pas décoratives : elles
empêchent une faute de frappe (un « 100 » au lieu de « 1 ») de vider la
campagne de simulation en quelques minutes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .config import Settings

# Bornes de sécurité : (minimum, maximum) — volontairement strictes.
BORNES: dict[str, tuple[float, float]] = {
    "montant_trade": (0.01, 1_000.0),
    "taille_max_position": (0.01, 1_000.0),
    "fraction_risque_pct": (0.1, 10.0),  # en pourcentage du capital
    "perte_max_journaliere": (1.0, 100_000.0),
    "trades_max_par_jour": (1, 500),
    "duree_expiration_s": (5, 3_600),
    "payout": (5.0, 99.0),  # en pourcentage
    # MT5 (Weltrade, compte DÉMO)
    "mt5_volume": (0.01, 5.0),          # en lot (0,01 = min)
    "mt5_sl_points": (1, 10_000),      # points (1 point = 0,00001)
    "mt5_tp_points": (1, 10_000),
}

MODES_CONNUS = ("DEMO", "OBSERVATION", "REEL")


class ReglageInvalide(ValueError):
    """Valeur hors bornes ou incohérente : la mise à jour est refusée."""


@dataclass
class Reglages:
    """Valeurs effectives du moteur (mutable, appliquée en direct)."""

    montant_trade: float
    taille_max_position: float
    fraction_risque_par_trade: float
    perte_max_journaliere: float
    trades_max_par_jour: int
    duree_expiration_s: int
    payout: float
    mode: str = "DEMO"
    # Flux de prix : "simule" (aucun réseau) ou "binance" (prix réels publics,
    # ordres papier). Indépendant du mode d'exécution.
    flux_prix: str = "simule"
    # MT5 (Weltrade, compte DÉMO) : valeurs effectives, modifiables à chaud.
    mt5_volume: float = 0.01
    mt5_sl_points: int = 50
    mt5_tp_points: int = 100

    # ------------------------------------------------------------------ #
    @classmethod
    def depuis_settings(cls, settings: Settings) -> "Reglages":
        return cls(
            montant_trade=settings.montant_trade,
            taille_max_position=settings.taille_max_position,
            fraction_risque_par_trade=settings.fraction_risque_par_trade,
            perte_max_journaliere=settings.perte_max_journaliere,
            trades_max_par_jour=settings.trades_max_par_jour,
            duree_expiration_s=settings.duree_expiration_s,
            payout=settings.payout,
            mode=settings.mode,
            flux_prix=settings.flux_prix,
            mt5_volume=settings.mt5_volume,
            mt5_sl_points=settings.mt5_sl_points,
            mt5_tp_points=settings.mt5_tp_points,
        )

    def copier_depuis(self, autre: "Reglages") -> None:
        """Applique en place : les références tenues par le moteur restent valides."""
        for cle, valeur in asdict(autre).items():
            setattr(self, cle, valeur)

    # ------------------------------------------------------------------ #
    def instantane(self) -> dict[str, Any]:
        return {
            "montant_trade": round(self.montant_trade, 2),
            "taille_max_position": round(self.taille_max_position, 2),
            "fraction_risque_pct": round(self.fraction_risque_par_trade * 100, 2),
            "perte_max_journaliere": round(self.perte_max_journaliere, 2),
            "trades_max_par_jour": self.trades_max_par_jour,
            "duree_expiration_s": self.duree_expiration_s,
            "payout_pct": round(self.payout * 100, 2),
            "mode": self.mode,
            "flux_prix": self.flux_prix,
            "flux_disponibles": ["simule", "binance"],
            "mt5_volume": round(self.mt5_volume, 2),
            "mt5_sl_points": self.mt5_sl_points,
            "mt5_tp_points": self.mt5_tp_points,
            "bornes": {cle: list(valeurs) for cle, valeurs in BORNES.items()},
            "modes_disponibles": list(MODES_CONNUS),
        }

    # ------------------------------------------------------------------ #
    @staticmethod
    def _lire_nombre(
        cle: str, valeur: Any, mini: float, maxi: float, entier: bool = False
    ) -> float:
        try:
            if isinstance(valeur, str):
                valeur = valeur.strip().replace(",", ".")
            nombre = float(valeur)
        except (TypeError, ValueError) as exc:
            raise ReglageInvalide(f"{cle} doit être un nombre (reçu : {valeur!r}).") from exc

        if nombre != nombre or nombre in (float("inf"), float("-inf")):
            raise ReglageInvalide(f"{cle} n'est pas un nombre valide.")
        if entier and abs(nombre - round(nombre)) > 1e-9:
            raise ReglageInvalide(f"{cle} doit être un entier.")
        if not (mini - 1e-9 <= nombre <= maxi + 1e-9):
            raise ReglageInvalide(
                f"{cle} doit être compris entre {mini} et {maxi} (reçu : {nombre})."
            )
        return round(nombre) if entier else float(nombre)

    def mis_a_jour(self, donnees: dict[str, Any]) -> "Reglages":
        """Construit de nouveaux réglages validés à partir d'un patch JSON.

        Seules les clés présentes sont modifiées. Les pourcentages arrivent en
        pourcentage côté UI et sont convertis en fraction côté moteur.
        """
        if not isinstance(donnees, dict):
            raise ReglageInvalide("Payload invalide : objet JSON attendu.")

        nouveau = Reglages(**asdict(self))

        correspondances = {
            "montant_trade": ("montant_trade", False),
            "taille_max_position": ("taille_max_position", False),
            "perte_max_journaliere": ("perte_max_journaliere", False),
            "trades_max_par_jour": ("trades_max_par_jour", True),
            "duree_expiration_s": ("duree_expiration_s", True),
            "mt5_volume": ("mt5_volume", False),
            "mt5_sl_points": ("mt5_sl_points", True),
            "mt5_tp_points": ("mt5_tp_points", True),
        }
        for cle_ui, (champ, entier) in correspondances.items():
            if cle_ui in donnees:
                mini, maxi = BORNES[cle_ui]
                setattr(
                    nouveau,
                    champ,
                    self._lire_nombre(cle_ui, donnees[cle_ui], mini, maxi, entier),
                )

        if "fraction_risque_pct" in donnees:
            mini, maxi = BORNES["fraction_risque_pct"]
            pct = self._lire_nombre("fraction_risque_pct", donnees["fraction_risque_pct"], mini, maxi)
            nouveau.fraction_risque_par_trade = pct / 100

        if "payout_pct" in donnees:
            mini, maxi = BORNES["payout"]
            pct = self._lire_nombre("payout_pct", donnees["payout_pct"], mini, maxi)
            nouveau.payout = pct / 100

        if "flux_prix" in donnees:
            flux = str(donnees["flux_prix"]).strip().lower()
            if flux not in ("simule", "binance"):
                raise ReglageInvalide(
                    f"Flux de prix inconnu : {flux!r}. Attendu : simule ou binance."
                )
            nouveau.flux_prix = flux

        if "mode" in donnees:
            mode = str(donnees["mode"]).strip().upper()
            if mode not in MODES_CONNUS:
                raise ReglageInvalide(
                    f"Mode inconnu : {mode!r}. Attendu : {', '.join(MODES_CONNUS)}."
                )
            nouveau.mode = mode

        self._valider_coherence(nouveau)
        return nouveau

    @staticmethod
    def _valider_coherence(r: "Reglages") -> None:
        if r.montant_trade > r.taille_max_position + 1e-9:
            raise ReglageInvalide(
                f"La mise par trade ({r.montant_trade:.2f} $) dépasse le plafond "
                f"de position ({r.taille_max_position:.2f} $)."
            )
        if r.perte_max_journaliere < r.montant_trade:
            raise ReglageInvalide(
                "La perte journalière maximale doit être au moins égale à la mise "
                "d'un trade, sinon aucun ordre ne pourra jamais être ouvert."
            )
        if r.payout <= 0:
            raise ReglageInvalide("Le payout doit être strictement positif.")
