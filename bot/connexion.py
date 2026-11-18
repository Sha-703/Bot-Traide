"""Couche de connexion : source de prix + WebSocket avec reconnexion.

Deux implémentations derrière la même interface `SourceDePrix` :

    - `FluxSynthetique` : génère un prix réaliste (marche aléatoire à
      volatilité stochastique). C'est le mode utilisé par défaut : il permet
      de développer, tester et démontrer sans toucher au compte réel.

    - `ConnexionPocketOption` : client WebSocket réel avec reconnexion
      exponentielle + jitter. Attention : Pocket Option n'expose AUCUNE API
      publique documentée. Le protocole est privé, change sans préavis et son
      usage peut violer les CGU de la plateforme. Le parseur de messages est
      donc volontairement isolé (`analyser_message`) et à adapter par
      l'utilisateur, qui en assume la responsabilité.

Aucune donnée sensible n'est loguée : le SSID est masqué avant tout affichage.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .journalisation import Journal

# Le SSID est un jeton de session : sa durée de vie est courte, mais tant
# qu'il est valide il donne accès au compte. Il ne doit jamais être logué.


class EtatConnexion(str, Enum):
    DECONNECTE = "DECONNECTE"
    CONNEXION = "CONNEXION"
    CONNECTE = "CONNECTE"
    RECONNEXION = "RECONNEXION"
    ERREUR = "ERREUR"


@dataclass(frozen=True)
class Tick:
    """Un point de prix horodaté."""

    actif: str
    prix: float
    horodatage: float  # epoch secondes (time.time())
    source: str = "synthetique"


class SourceDePrix(ABC):
    """Contrat commun aux flux de prix."""

    etat: EtatConnexion = EtatConnexion.DECONNECTE
    # Nom de provenance affiché dans l'interface.
    nom: str = "indisponible"
    # Instrument effectivement suivi (surchargé par les flux concrets).
    actif: str = ""
    # Horloge virtuelle : seuls les flux de simulation l'utilisent.
    pas_virtuel: float | None = None

    @abstractmethod
    def flux(self) -> AsyncIterator[Tick]:
        """Générateur asynchrone de ticks."""

    async def fermer(self) -> None:  # pragma: no cover - no-op par défaut
        """Libère les ressources réseau."""
        return None


# --------------------------------------------------------------------------- #
# 1. Flux synthétique (démo / tests / dashboard)
# --------------------------------------------------------------------------- #
class FluxSynthetique(SourceDePrix):
    """Marche aléatoire à régimes : tendance, bruit, retour à la moyenne.

    Ce n'est pas un modèle de marché : c'est un générateur de données
    reproductible (via `graine`) pour valider la plomberie du bot.
    """

    nom = "synthetique"

    def __init__(
        self,
        actif: str = "EURUSD",
        prix_initial: float = 1.0850,
        intervalle_s: float = 1.0,
        graine: int | None = None,
        volatilite: float = 0.00012,
        pas_virtuel: float | None = None,
    ) -> None:
        self.actif = actif
        self.prix = prix_initial
        self.intervalle_s = intervalle_s
        self.volatilite = volatilite
        self._rng = random.Random(graine)
        self._tendance = 0.0
        self._ticks_restants_regime = 0
        # Ancre glissante : évite une dérive infinie SANS créer de retour à
        # la moyenne artificiel qui offrirait un avantage fictif à la stratégie.
        self._ancre = prix_initial
        # Horloge virtuelle : les ticks avancent le temps sans attendre.
        # Utilisée pour les campagnes de simulation (des milliers de trades
        # en quelques secondes) sans modifier la logique du moteur.
        self.pas_virtuel = pas_virtuel
        self._temps_virtuel: float | None = None
        self.etat = EtatConnexion.CONNECTE

    def _nouveau_regime(self) -> None:
        """Change de régime : légère tendance ou retour à l'ancre.

        La dérive est volontairement FAIBLE (0,15 × volatilité) : le flux doit
        rester proche d'une marche aléatoire, sans autocorrélation assez forte
        pour offrir un avantage artificiel à une stratégie de suivi de tendance.
        """
        if self._rng.random() < 0.35:
            self._tendance = self._rng.uniform(-1.0, 1.0) * self.volatilite * 0.15
        else:
            self._tendance = 0.0
        self._ticks_restants_regime = self._rng.randint(40, 180)

    async def flux(self) -> AsyncIterator[Tick]:
        self.etat = EtatConnexion.CONNECTE
        while True:
            if self._ticks_restants_regime <= 0:
                self._nouveau_regime()

            # Ancrage glissant très lent : borne la dérive sans biais directionnel.
            self._ancre += (self.prix - self._ancre) * 0.0005
            rappel = (self._ancre - self.prix) * 0.002
            choc = self._rng.gauss(0, self.volatilite)
            self.prix = max(0.0001, self.prix + self._tendance + rappel + choc)
            self._ticks_restants_regime -= 1

            if self.pas_virtuel:  # mode campagne : temps accéléré, pas d'attente
                if self._temps_virtuel is None:
                    self._temps_virtuel = time.time()
                self._temps_virtuel += self.pas_virtuel
                horodatage = self._temps_virtuel
            else:
                # Secondes epoch : cohérent avec l'horloge du moteur
                # (progression des ordres, échéances affichées).
                horodatage = time.time()

            yield Tick(
                actif=self.actif,
                prix=round(self.prix, 6),
                horodatage=horodatage,
                source="synthetique",
            )
            if not self.pas_virtuel:
                await asyncio.sleep(self.intervalle_s)


# --------------------------------------------------------------------------- #
# 2. Flux Binance public (lecture seule, sans compte)
# --------------------------------------------------------------------------- #
URL_BINANCE = "wss://stream.binance.com:9443/ws/{flux}"
URL_BINANCE_MIROIR = "wss://data-stream.binance.vision/ws/{flux}"


class FluxBinance(SourceDePrix):
    """Prix réels depuis le flux public Binance : aucun compte, aucune clé.

    Objectif : mesurer la stratégie sur de vrais prix, en ordres papier.
    Aucun ordre n'est jamais transmis — ce flux est strictement en lecture.

    Binance émet plusieurs trades par seconde ; on ne conserve qu'un prix par
    `intervalle_s` (1 s par défaut), sinon la stratégie serait noyée.
    """

    nom = "binance"
    pas_virtuel = None

    def __init__(
        self,
        symbole: str = "BTCUSDT",
        intervalle_s: float = 1.0,
        journal: Journal | None = None,
        max_tentatives: int = 0,  # 0 = reconnexion illimitée
        backoff_initial: float = 1.0,
        backoff_max: float = 30.0,
    ) -> None:
        self.actif = symbole.strip().upper()
        self.intervalle_s = max(0.2, float(intervalle_s))
        self.journal = journal
        self.max_tentatives = max_tentatives
        self.backoff_initial = backoff_initial
        self.backoff_max = backoff_max
        self.etat = EtatConnexion.DECONNECTE
        self.dernier_prix: float | None = None
        self.tentatives = 0
        self._arret = False

    # ------------------------------------------------------------------ #
    def _log(self, niveau: str, message: str, **extra: Any) -> None:
        if self.journal:
            self.journal.evenement(niveau, "connexion", message, **extra)

    def _url(self, miroir: bool = False) -> str:
        flux = f"{self.actif.lower()}@aggTrade"
        gabarit = URL_BINANCE_MIROIR if miroir else URL_BINANCE
        return gabarit.format(flux=flux)

    @staticmethod
    def extraire_prix(brut: str) -> float | None:
        """Lit une trame aggTrade (`{"e":"aggTrade","p":"67234.10",...}`)."""
        try:
            charge = json.loads(brut)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(charge, dict):
            return None
        valeur = charge.get("p") or charge.get("c") or charge.get("price")
        if valeur is None:
            return None
        try:
            prix = float(valeur)
        except (TypeError, ValueError):
            return None
        return prix if prix > 0 else None

    # ------------------------------------------------------------------ #
    async def flux(self) -> AsyncIterator[Tick]:
        try:
            import websockets  # import local : dépendance optionnelle
        except ImportError as exc:  # pragma: no cover
            self.etat = EtatConnexion.ERREUR
            raise RuntimeError(
                "Le paquet 'websockets' est requis pour le flux réel : "
                "pip install websockets"
            ) from exc

        backoff = self.backoff_initial
        while not self._arret:
            if self.max_tentatives and self.tentatives >= self.max_tentatives:
                break
            self.tentatives += 1
            self.etat = (
                EtatConnexion.CONNEXION
                if self.tentatives == 1
                else EtatConnexion.RECONNEXION
            )
            # Le miroir (données publiques) sert de repli si l'hôte principal
            # est filtré par le réseau.
            url = self._url(miroir=self.tentatives % 2 == 0)
            self._log("info", f"Connexion Binance #{self.tentatives} ({self.actif})")
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_queue=1024,
                ) as ws:
                    self.etat = EtatConnexion.CONNECTE
                    backoff = self.backoff_initial
                    self._log("info", f"Flux Binance connecté : {self.actif}")
                    dernier_emis = 0.0
                    while not self._arret:
                        brut = await asyncio.wait_for(
                            ws.recv(), timeout=max(10.0, self.intervalle_s * 10)
                        )
                        prix = self.extraire_prix(brut)
                        if prix is None:
                            continue
                        maintenant = time.monotonic()
                        if maintenant - dernier_emis < self.intervalle_s:
                            continue
                        dernier_emis = maintenant
                        self.dernier_prix = prix
                        yield Tick(
                            actif=self.actif,
                            prix=prix,
                            horodatage=time.time(),
                            source="binance",
                        )
            except asyncio.CancelledError:
                self.etat = EtatConnexion.DECONNECTE
                raise
            except Exception as exc:  # réseau, coupure, flux muet…
                self.etat = EtatConnexion.ERREUR
                attente = min(backoff, self.backoff_max) * random.uniform(0.8, 1.3)
                self._log(
                    "erreur",
                    f"Flux Binance interrompu : {type(exc).__name__}. "
                    f"Reconnexion dans {attente:.1f}s",
                    detail=str(exc)[:200],
                )
                await asyncio.sleep(attente)
                backoff *= 2

        if self._arret:
            self.etat = EtatConnexion.DECONNECTE
            return
        self.etat = EtatConnexion.ERREUR
        self._log("erreur", "Nombre maximal de tentatives Binance atteint.")
        raise ConnectionError(
            f"Flux Binance interrompu après {self.tentatives} tentatives."
        )

    async def fermer(self) -> None:
        self._arret = True
        self.etat = EtatConnexion.DECONNECTE


# --------------------------------------------------------------------------- #
# 3. Client WebSocket réel avec reconnexion automatique
# --------------------------------------------------------------------------- #
class ConnexionPocketOption(SourceDePrix):
    """Client WebSocket tolérant aux coupures.

    Fonctionnement :
        - connexion à l'URL fournie (variable d'env. `WS_URL`) ;
        - envoi d'un message d'authentification contenant le SSID ;
        - lecture continue, parsing via `analyser_message` ;
        - en cas d'erreur : backoff exponentiel plafonné + jitter, nombre de
          tentatives limité, état publié pour le dashboard.

    ATTENTION : l'endpoint et le format des messages ne sont pas publics.
    Vous devez fournir un `analyser_message` conforme à votre bibliothèque
    (BinaryOptionsToolsV2, pocketoptionapi-async, ...) et vérifier le code de
    cette bibliothèque avant usage (voir SECURITY.md).
    """

    nom = "pocket_option"

    def __init__(
        self,
        ssid: str,
        actif: str = "EURUSD",
        url: str = "wss://api-eu.po.market/socket.io/?EIO=4&transport=websocket",
        journal: Journal | None = None,
        max_tentatives: int = 8,
        backoff_initial: float = 1.0,
        backoff_max: float = 30.0,
        analyser_message: Any | None = None,
    ) -> None:
        if not ssid:
            raise ValueError("SSID manquant : connexion réelle impossible.")
        self.ssid = ssid
        self.actif = actif
        self.url = url
        self.journal = journal
        self.max_tentatives = max_tentatives
        self.backoff_initial = backoff_initial
        self.backoff_max = backoff_max
        self._analyser = analyser_message or self.analyser_message
        self.etat = EtatConnexion.DECONNECTE
        self.tentatives = 0

    # ------------------------------------------------------------------ #
    def _log(self, niveau: str, message: str, **extra: Any) -> None:
        if self.journal:
            self.journal.evenement(niveau, "connexion", message, **extra)

    def message_authentification(self) -> str:
        """Payload de connexion. À adapter au protocole réellement observé.

        Le SSID est transmis dans le message d'auth — il ne doit jamais
        apparaître dans un log, un CSV ou une capture d'écran.
        """
        return json.dumps(
            {
                "action": "authentifier",
                "ssid": self.ssid,
                "actif": self.actif,
                "plateforme": "pocket_option",
            }
        )

    def analyser_message(self, brut: str) -> Tick | None:
        """Convertit un message brut en Tick.

        Implémentation de référence : accepte un JSON du type
        {"actif": "EURUSD", "prix": 1.08501, "ts": 1720000000}.
        Retourne None si le message n'est pas un prix exploitable.
        """
        try:
            if brut.startswith("42"):
                brut = brut[2:]
            charge = json.loads(brut)
        except (json.JSONDecodeError, TypeError):
            return None
        prix = charge.get("prix") or charge.get("price") or charge.get("close")
        if prix is None:
            return None
        try:
            return Tick(
                actif=str(charge.get("actif") or charge.get("asset") or self.actif),
                prix=float(prix),
                horodatage=float(charge.get("ts") or time.time()),
                source="pocket_option",
            )
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------ #
    async def flux(self) -> AsyncIterator[Tick]:
        """Boucle de connexion avec reconnexion automatique."""
        try:
            import websockets  # import local : dépendance optionnelle
        except ImportError as exc:  # pragma: no cover
            self.etat = EtatConnexion.ERREUR
            raise RuntimeError(
                "Le paquet 'websockets' est requis pour le flux réel : "
                "pip install websockets"
            ) from exc

        backoff = self.backoff_initial
        while self.tentatives < self.max_tentatives:
            self.tentatives += 1
            self.etat = (
                EtatConnexion.CONNEXION if self.tentatives == 1 else EtatConnexion.RECONNEXION
            )
            self._log("info", f"Tentative de connexion #{self.tentatives}", url=self.url)
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_queue=512,
                ) as ws:
                    self.etat = EtatConnexion.CONNECTE
                    backoff = self.backoff_initial
                    self._log("info", "WebSocket connecté", ssid=self.ssid[:4] + "…")
                    await ws.send(self.message_authentification())

                    async for brut in ws:
                        tick = self._analyser(brut)
                        if tick is not None:
                            yield tick
            except asyncio.CancelledError:
                self.etat = EtatConnexion.DECONNECTE
                raise
            except Exception as exc:  # réseau, handshake, protocole...
                self.etat = EtatConnexion.ERREUR
                attente = min(backoff, self.backoff_max)
                attente = attente * random.uniform(0.8, 1.3)  # jitter anti-thundering herd
                self._log(
                    "erreur",
                    f"Coupure : {type(exc).__name__}. Reconnexion dans {attente:.1f}s",
                    detail=str(exc)[:200],
                )
                await asyncio.sleep(attente)
                backoff *= 2

        self.etat = EtatConnexion.ERREUR
        self._log("erreur", "Nombre maximal de tentatives atteint. Flux arrêté.")
        raise ConnectionError(
            f"Impossible de maintenir la connexion après {self.max_tentatives} tentatives."
        )

    async def fermer(self) -> None:
        self.etat = EtatConnexion.DECONNECTE


def construire_source(settings: Any, journal: Journal) -> SourceDePrix:
    """Fabrique la source de prix selon le mode et la disponibilité du SSID."""
    if settings.mode_reel:
        return ConnexionPocketOption(
            ssid=settings.ssid or "",
            actif=settings.actif,
            journal=journal,
        )
    if getattr(settings, "flux_prix", "simule") == "binance":
        return FluxBinance(
            symbole=getattr(settings, "actif_binance", "BTCUSDT"),
            intervalle_s=settings.intervalle_tick_s
            / max(settings.facteur_vitesse, 0.01),
            journal=journal,
        )
    return FluxSynthetique(
        actif=settings.actif,
        intervalle_s=settings.intervalle_tick_s / max(settings.facteur_vitesse, 0.01),
        graine=settings.graine_aleatoire,
        # Au-delà de 50x, on bascule sur une horloge virtuelle : la campagne
        # de test devient instantanée au lieu de durer des heures.
        pas_virtuel=settings.intervalle_tick_s if settings.facteur_vitesse >= 50 else None,
    )
