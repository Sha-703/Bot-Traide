"""Journalisation : la mémoire froide du bot.

Deux sorties distinctes :
    1. `trades.csv`  -> une ligne par trade résolu (audit, tableur, backtest).
    2. `evenements.jsonl` -> flux d'événements horodatés (connexion, refus de
       risque, erreurs) pour reconstituer une session a posteriori.

Le SSID et les secrets ne doivent JAMAIS transiter par ce module.
"""

from __future__ import annotations

import csv
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COLONNES = [
    "horodatage_utc",
    "mode",
    "actif",
    "direction",
    "montant",
    "expiration_s",
    "prix_entree",
    "prix_sortie",
    "resultat",
    "pnl",
    "solde_apres",
    "strategie",
    "commentaire",
]

RESULTATS = ("GAGNANT", "PERDANT", "REMBOURSE", "EN_COURS")


@dataclass
class TradeJournalise:
    horodatage_utc: str
    mode: str
    actif: str
    direction: str
    montant: float
    expiration_s: int
    prix_entree: float
    prix_sortie: float | None
    resultat: str
    pnl: float
    solde_apres: float
    strategie: str
    commentaire: str = ""

    def dict_public(self) -> dict[str, Any]:
        """Représentation JSON pour le dashboard (déjà sans secret)."""
        data = asdict(self)
        data["horodatage_utc"] = self.horodatage_utc
        return data

    @property
    def gagnant(self) -> bool:
        return self.resultat == "GAGNANT"


class Journal:
    """Écriture CSV thread-safe + tampon mémoire pour l'API du dashboard."""

    def __init__(self, dossier: Path, nom_fichier: str = "trades.csv") -> None:
        self.dossier = Path(dossier)
        self.dossier.mkdir(parents=True, exist_ok=True)
        self.chemin_trades = self.dossier / nom_fichier
        self.chemin_evenements = self.dossier / "evenements.jsonl"
        self._verrou = threading.Lock()
        # Le journal relit son historique : après un redémarrage de la console,
        # le tableau et les statistiques affichent les trades déjà enregistrés.
        self._trades: list[TradeJournalise] = self._charger()
        self._initialiser_fichier()

    # ------------------------------------------------------------------ #
    def _charger(self) -> list[TradeJournalise]:
        """Relit `trades.csv` s'il existe (lignes invalides ignorées)."""
        trades: list[TradeJournalise] = []
        try:
            with self.chemin_trades.open(
                "r", newline="", encoding="utf-8-sig"
            ) as f:
                for ligne in csv.DictReader(f, delimiter=";"):
                    if not ligne:
                        continue
                    # Une ligne partiellement cassée est ignorée (pas devinée).
                    if not (ligne.get("horodatage_utc") or "").strip():
                        continue
                    if (ligne.get("resultat") or "") not in RESULTATS:
                        continue
                    try:
                        prix_sortie = ligne.get("prix_sortie") or ""
                        trades.append(
                            TradeJournalise(
                                horodatage_utc=ligne["horodatage_utc"],
                                mode=ligne.get("mode") or "DEMO",
                                actif=ligne.get("actif") or "",
                                direction=ligne.get("direction") or "",
                                montant=float(ligne.get("montant") or 0),
                                expiration_s=int(float(ligne.get("expiration_s") or 0)),
                                prix_entree=float(ligne.get("prix_entree") or 0),
                                prix_sortie=float(prix_sortie) if prix_sortie else None,
                                resultat=ligne.get("resultat") or "",
                                pnl=float(ligne.get("pnl") or 0),
                                solde_apres=float(ligne.get("solde_apres") or 0),
                                strategie=ligne.get("strategie") or "",
                                commentaire=ligne.get("commentaire") or "",
                            )
                        )
                    except (KeyError, ValueError, TypeError):
                        continue
        except FileNotFoundError:
            pass
        return trades

    # ------------------------------------------------------------------ #
    def _initialiser_fichier(self) -> None:
        if not self.chemin_trades.exists():
            with self.chemin_trades.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f, delimiter=";").writerow(COLONNES)

    # ------------------------------------------------------------------ #
    @staticmethod
    def maintenant() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def enregistrer(self, trade: TradeJournalise) -> None:
        """Ajoute un trade au CSV (append) et au tampon mémoire."""
        with self._verrou:
            with self.chemin_trades.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f, fieldnames=COLONNES, delimiter=";", extrasaction="ignore"
                )
                writer.writerow(asdict(trade))
            self._trades.append(trade)

    def archiver_campagne(self) -> Path | None:
        """Déplace `trades.csv` dans `campagnes/` et repart d'un journal vide.

        Rien n'est supprimé : le fichier est renommé avec un horodatage et reste
        consultable. Les statistiques affichées ne portent alors que sur la
        nouvelle campagne — condition pour que les chiffres veuillent dire
        quelque chose. Retourne le chemin de l'archive, ou None si vide.
        """
        with self._verrou:
            if not self._trades:
                return None
            dossier = self.dossier / "campagnes"
            dossier.mkdir(parents=True, exist_ok=True)
            base = time.strftime("%Y%m%d-%H%M%S")
            cible = dossier / f"trades-{base}.csv"
            suffixe = 2
            while cible.exists():  # deux campagnes dans la même seconde
                cible = dossier / f"trades-{base}-{suffixe}.csv"
                suffixe += 1
            if self.chemin_trades.exists():
                self.chemin_trades.replace(cible)
            self._trades.clear()
            with self.chemin_trades.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f, delimiter=";").writerow(COLONNES)
        self.evenement("info", "journal", f"Campagne archivée : {cible.name}")
        return cible

    def evenement(self, niveau: str, source: str, message: str, **extra: Any) -> None:
        """Écrit une ligne JSON dans le journal d'événements."""
        ligne = {
            "ts": self.maintenant(),
            "niveau": niveau,
            "source": source,
            "message": message,
            **extra,
        }
        with self._verrou:
            with self.chemin_evenements.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ligne, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ #
    @property
    def trades(self) -> list[TradeJournalise]:
        with self._verrou:
            return list(self._trades)

    def statistiques(self) -> dict[str, Any]:
        """Indicateurs de performance calculés sur les trades résolus."""
        trades = [t for t in self.trades if t.resultat in ("GAGNANT", "PERDANT", "REMBOURSE")]
        if not trades:
            return {
                "total": 0,
                "gagnants": 0,
                "perdants": 0,
                "taux_reussite": 0.0,
                "pnl_total": 0.0,
                "gain_moyen": 0.0,
                "perte_moyenne": 0.0,
                "ratio_gain_perte": 0.0,
                "esperance": 0.0,
                "drawdown_max": 0.0,
                "profit_factor": 0.0,
                "courbe_solde": [],
            }

        gagnants = [t for t in trades if t.pnl > 0]
        perdants = [t for t in trades if t.pnl < 0]
        pnl_total = sum(t.pnl for t in trades)
        gain_moyen = sum(t.pnl for t in gagnants) / len(gagnants) if gagnants else 0.0
        perte_moyenne = abs(sum(t.pnl for t in perdants) / len(perdants)) if perdants else 0.0
        gains_bruts = sum(t.pnl for t in gagnants)
        pertes_brutes = abs(sum(t.pnl for t in perdants))

        # Courbe de solde + drawdown maximal (en valeur).
        courbe: list[dict[str, Any]] = []
        solde = trades[0].solde_apres - trades[0].pnl
        sommet = solde
        drawdown_max = 0.0
        for t in trades:
            solde += t.pnl
            sommet = max(sommet, solde)
            drawdown_max = max(drawdown_max, sommet - solde)
            courbe.append({"i": len(courbe), "solde": round(solde, 2)})

        return {
            "total": len(trades),
            "gagnants": len(gagnants),
            "perdants": len(perdants),
            "taux_reussite": round(100 * len(gagnants) / len(trades), 1),
            "pnl_total": round(pnl_total, 2),
            "gain_moyen": round(gain_moyen, 2),
            "perte_moyenne": round(perte_moyenne, 2),
            "ratio_gain_perte": round(gain_moyen / perte_moyenne, 2) if perte_moyenne else 0.0,
            "esperance": round(pnl_total / len(trades), 2),
            "drawdown_max": round(drawdown_max, 2),
            "profit_factor": round(gains_bruts / pertes_brutes, 2) if pertes_brutes else 0.0,
            "courbe_solde": courbe,
        }
