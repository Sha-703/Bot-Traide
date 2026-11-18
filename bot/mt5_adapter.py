"""Adaptateur Weltrade / MetaTrader 5 — LECTURE DEMO (aucune exécution).

Alimente le bot avec deux données réelles du compte démo Weltrade :

    - le **solde** et les infos du compte (login, serveur, levier…) ;
    - les **ticks** du symbole choisi (EURUSD_i par défaut).

Aucun ordre n'est envoyé : l'exécution réelle (SL/TP, enregistrement des
closes en PnL réel) sera une étape séparée et assumée. Ce module est
strictement en lecture seule et ne s'active que si `.env` contient
`MT5_DEMO=true`.

La bibliothèque MetaQuotes (`MetaTrader5`) se connecte à un **terminal déjà
installé et démarré** — pas besoin de mot de passe dans le bot : le compte
est celui choisi dans le terminal Weltrade MT5.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .journalisation import Journal
from .solde import SourceSolde
from .connexion import SourceDePrix, Tick, EtatConnexion

CHEMIN_PAR_DEFAUT = r"C:\Program Files\Weltrade MT5 Terminal\terminal64.exe"


class MT5Indisponible(RuntimeError):
    """Le terminal MT5 n'est pas installé, pas démarré, ou pas connecté."""


def chemin_mt5(chemin: str | None = None) -> str:
    valeur = (chemin or "").strip()
    return valeur or CHEMIN_PAR_DEFAUT


def _charger_mt5():
    try:
        import MetaTrader5 as mt5  # pip install MetaTrader5
    except ImportError as exc:
        raise MT5Indisponible(
            "Le paquet 'MetaTrader5' n'est pas installé : pip install MetaTrader5"
        ) from exc
    return mt5


def _connecter(chemin: str | None = None):
    mt5 = _charger_mt5()
    # Tentative en mode automatique (utilise le terminal déjà démarré),
    # sinon avec le chemin explicite.
    if mt5.initialize():
        return mt5
    if mt5.initialize(path=chemin_mt5(chemin)):
        return mt5
    code, detail = mt5.last_error()
    try:
        mt5.shutdown()
    except Exception:
        pass
    raise MT5Indisponible(
        f"Terminal MT5 indisponible (code {code} : {detail}). "
        "Ouvrez le terminal Weltrade MT5 et connectez-vous au compte demo, "
        f"et vérifiez le chemin : {chemin_mt5(chemin)}"
    )


def informations_compte(chemin: str | None = None) -> dict[str, Any]:
    # Pas de shutdown() ici : tous les appels MT5 du process partagent une
    # seule connexion IPC ; la fermer couperait le flux de prix actif.
    mt5 = _connecter(chemin)
    info = mt5.account_info()
    if info is None:
        raise MT5Indisponible(
            "Terminal démarré mais aucun compte connecté. Ouvrez le "
            "terminal Weltrade MT5 et connectez-vous au compte demo."
        )
    return {
        "login": info.login,
        "devise": info.currency,
        "serveur": info.server,
        "levier": info.leverage,
        "balance": round(info.balance, 2),
        "equity": round(info.equity, 2),
        "nom": info.name,
        "demo": True,  # ce module est réservé aux comptes demo
    }


def _tick(mt5, symbole: str):
    """Retourne le tick muet… ou lève MT5Indisponible si muet durablement."""
    info = mt5.symbol_info_tick(symbole)
    if info is None:
        raise MT5Indisponible(f"Symbole introuvable ou cotation muet : {symbole}")
    prix = info.bid if info.bid > 0 else info.last
    if prix <= 0:
        raise MT5Indisponible(f"Tick muet pour {symbole} (marché fermé ?).")
    return info


class SourceMT5(SourceDePrix):
    """Ticks réels du terminal Weltrade MT5 (lecture seule)."""

    nom = "mt5_demo"
    pas_virtuel = None

    def __init__(
        self,
        symbole: str = "EURUSD_i",
        intervalle_s: float = 1.0,
        chemin: str | None = None,
        journal: Journal | None = None,
    ) -> None:
        self.actif = symbole.strip()
        self.intervalle_s = max(0.2, float(intervalle_s))
        self.chemin = chemin
        self.journal = journal
        self.etat = EtatConnexion.DECONNECTE
        self._arret = False
        self._echecs_consecutifs = 0
        self._actif_selectionne = False

    async def flux(self):
        # Une seule connexion au terminal pour toute la durée du flux :
        # l'ouverture/fermeture à chaque tick serait lente et instable.
        mt5 = _charger_mt5()
        if not mt5.initialize(path=chemin_mt5(self.chemin)):
            if not mt5.initialize():
                code, detail = mt5.last_error()
                self.etat = EtatConnexion.ERREUR
                raise MT5Indisponible(
                    f"Terminal MT5 indisponible (code {code} : {detail})."
                )
        self.etat = EtatConnexion.CONNEXION
        # Sélection du symbole une seule fois (le terminal garde le flag).
        mt5.symbol_select(self.actif, True)
        try:
            while not self._arret:
                try:
                    info = _tick(mt5, self.actif)
                except MT5Indisponible as exc:
                    self._echecs_consecutifs += 1
                    self.etat = EtatConnexion.ERREUR
                    if self.journal and self._echecs_consecutifs == 1:
                        self.journal.evenement(
                            "avertissement",
                            "mt5",
                            f"Tick muet ({self.actif}) : {exc}. "
                            "Nouvel essai automatiquement.",
                        )
                    if self._echecs_consecutifs >= 30:
                        # Inspect le compte, puis abandonne.
                        raise MT5Indisponible(
                            f"30 ticks muets consécutifs pour {self.actif} : "
                            "terminal ou symbole incorrect (marché fermé ou marché indisponible)."
                        )
                    await asyncio.sleep(self.intervalle_s)
                    continue
                self._echecs_consecutifs = 0
                self.etat = EtatConnexion.CONNECTE
                if info.bid > 0 and info.ask > 0:
                    prix = round((info.bid + info.ask) / 2.0, 6)
                elif info.bid > 0:
                    prix = round(info.bid, 6)
                else:
                    prix = round(info.last, 6)
                yield Tick(
                    actif=self.actif,
                    prix=prix,
                    horodatage=float(info.time),
                    source="mt5_demo",
                )
                await asyncio.sleep(self.intervalle_s)
        finally:
            try:
                mt5.shutdown()
            except Exception:
                pass
            self.etat = EtatConnexion.DECONNECTE

    async def fermer(self) -> None:
        self._arret = True
        self.etat = EtatConnexion.DECONNECTE


class SoldeMT5Demo(SourceSolde):
    """Solde du compte DÉMO Weltrade MT5 — lecture seule, jamais inventé."""

    nom = "MT5_DEMO"

    def __init__(self, chemin: str | None = None) -> None:
        self.chemin = chemin

    async def lire(self):
        boucle = asyncio.get_running_loop()
        try:
            info = await boucle.run_in_executor(
                None, informations_compte, self.chemin
            )
        except MT5Indisponible as exc:
            return None, f"Solde MT5 indisponible : {exc}"
        return (
            info["balance"],
            f"Compte DÉMO Weltrade MT5 — #{info['login']} · {info['serveur']} · "
            f"{info['devise']} · levier 1:{info['levier']}.",
        )
