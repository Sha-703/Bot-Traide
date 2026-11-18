"""Gestion de la session plateforme — SSID en mémoire uniquement.

Principes de conception :
    - On ne demande JAMAIS d'email ni de mot de passe. Le SSID est un jeton de
      session temporaire, pas un identifiant de compte.
    - Rien n'est écrit sur disque : ni fichier, ni log, ni CSV. Une coupure du
      serveur efface la session, c'est voulu.
    - L'API ne renvoie qu'un aperçu masqué (`abcd…wxyz`).
    - « Tester la connexion » ouvre réellement un WebSocket et rapporte ce qui
      se passe, y compris l'échec. Aucune fausse réussite : si le protocole
      n'est pas celui attendu, on le dit.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Endpoint par défaut (hôte démo, fourni par vous). Le mode réel utilisera
# l'hôte correspondant une fois le trafic capturé analysé. Surchargeable via WS_URL.
URL_PAR_DEFAUT = os.getenv("WS_URL") or (
    "wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket"
)

LONGUEUR_MIN = 8
LONGUEUR_MAX = 4096


class EtatSession(str, Enum):
    VIDE = "VIDE"
    EN_MEMOIRE = "EN_MEMOIRE"
    JOIGNABLE = "JOIGNABLE"  # serveur joint, jeton NON validé
    VALIDEE = "VALIDEE"  # réservé à un adaptateur qui sait vérifier le jeton
    ECHEC = "ECHEC"


class SessionInvalide(ValueError):
    """Le jeton fourni n'a pas la forme attendue."""


@dataclass
class GestionnaireSession:
    """Détient le SSID en RAM et expose un état lisible pour la console."""

    url: str = URL_PAR_DEFAUT
    etat: EtatSession = EtatSession.VIDE
    message: str = "Aucune session : le flux simulé est utilisé."
    derniere_erreur: str | None = None
    tentatives: int = 0
    origine: str = "manuel"  # "manuel" (jeton collé) ou "navigateur" (login supervisé)
    _ssid: str | None = field(default=None, repr=False)
    _verrou: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    # ------------------------------------------------------------------ #
    @property
    def configuree(self) -> bool:
        return bool(self._ssid)

    @property
    def ssid(self) -> str | None:
        """Accès interne au jeton (jamais sérialisé, jamais journalisé)."""
        return self._ssid

    def apercu(self) -> str:
        """Aperçu sûr : jamais plus de 8 caractères, toujours tronqué."""
        if not self._ssid:
            return ""
        if len(self._ssid) <= 8:
            return "•" * len(self._ssid)
        return f"{self._ssid[:4]}…{self._ssid[-4:]}"

    @staticmethod
    def valider(ssid: str) -> str:
        """Nettoie et valide le jeton. Lève SessionInvalide sinon."""
        propre = (ssid or "").strip()
        if not propre:
            raise SessionInvalide("Le SSID est vide.")
        if len(propre) < LONGUEUR_MIN:
            raise SessionInvalide(
                f"SSID trop court ({len(propre)} caractères, minimum {LONGUEUR_MIN}). "
                "Un SSID complet ressemble à une longue chaîne encodée."
            )
        if len(propre) > LONGUEUR_MAX:
            raise SessionInvalide("SSID anormalement long : vérifiez le copier-coller.")
        if any(c in propre for c in "\r\n\t "):
            raise SessionInvalide(
                "Le SSID ne doit contenir ni espace ni retour à la ligne."
            )
        return propre

    # ------------------------------------------------------------------ #
    def enregistrer(self, ssid: str, origine: str = "manuel") -> dict[str, Any]:
        """Place le jeton en mémoire (aucune écriture disque)."""
        self._ssid = self.valider(ssid)
        self.origine = origine if origine in ("manuel", "navigateur") else "manuel"
        self.etat = EtatSession.EN_MEMOIRE
        self.derniere_erreur = None
        if self.origine == "navigateur":
            self.message = (
                "Session ouverte via votre navigateur : le mot de passe n'est "
                "jamais enregistré. Le mode réel reste verrouillé."
            )
        else:
            self.message = (
                "SSID conservé en mémoire pour cette session uniquement. "
                "Aucun ordre réel ne sera transmis : le mode réel reste verrouillé."
            )
        return self.instantane()

    def oublier(self) -> dict[str, Any]:
        """Efface le jeton de la mémoire vive."""
        self._ssid = None
        self.origine = "manuel"
        self.etat = EtatSession.VIDE
        self.derniere_erreur = None
        self.tentatives = 0
        self.message = "Session effacée. Le flux simulé est utilisé."
        return self.instantane()

    def instantane(self) -> dict[str, Any]:
        """Vue publique : aucun secret, seulement un aperçu masqué."""
        return {
            "configuree": self.configuree,
            "apercu": self.apercu(),
            "etat": self.etat.value,
            "message": self.message,
            "derniere_erreur": self.derniere_erreur,
            "tentatives": self.tentatives,
            "url": self.url,
            "origine": self.origine,
            "persistant": False,
        }

    # ------------------------------------------------------------------ #
    async def tester(self, delai: float = 8.0) -> dict[str, Any]:
        """Ouvre un WebSocket réel et rapporte honnêtement le résultat.

        Trois issues possibles :
            - réseau injoignable / TLS refusé -> ECHEC avec le détail ;
            - connexion ouverte                 -> JOIGNABLE (le serveur répond,
              mais le SSID n'est PAS validé : le protocole d'authentification
              privé reste à adapter) ;
            - aucune trame                      -> JOIGNABLE avec réserve.

        On n'affiche jamais « connecté » sur la seule base d'un handshake
        socket.io : ce dernier arrive AVANT toute authentification.
        """
        if not self._ssid:
            raise SessionInvalide("Aucun SSID en mémoire : renseignez-le d'abord.")

        try:
            import websockets
        except ImportError:  # pragma: no cover
            self.etat = EtatSession.ECHEC
            self.message = "Le paquet 'websockets' est requis pour tester : pip install websockets"
            return self.instantane()

        async with self._verrou:
            self.tentatives += 1
            self.derniere_erreur = None
            try:
                async with websockets.connect(
                    self.url,
                    open_timeout=delai,
                    close_timeout=3,
                    ping_interval=None,
                ) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "action": "authentifier",
                                "ssid": self._ssid,
                                "plateforme": "pocket_option",
                            }
                        )
                    )
                    try:
                        brut = await asyncio.wait_for(ws.recv(), timeout=6.0)
                    except asyncio.TimeoutError:
                        self.etat = EtatSession.JOIGNABLE
                        self.message = (
                            "WebSocket ouvert, mais aucune trame reçue en 6 s. "
                            "Le SSID n'est pas validé : le format d'authentification "
                            "reste à adapter."
                        )
                    else:
                        texte = (
                            brut.decode("utf-8", "replace")
                            if isinstance(brut, (bytes, bytearray))
                            else str(brut)
                        )
                        self.etat = EtatSession.JOIGNABLE
                        if texte.startswith("0{"):
                            self.message = (
                                "Serveur joignable : handshake socket.io reçu. "
                                "Le SSID n'est PAS validé — l'authentification "
                                "réelle reste à adapter."
                            )
                        else:
                            self.message = (
                                f"Serveur joignable, réponse de {len(texte)} octets. "
                                "Le SSID n'est pas validé : protocole à adapter."
                            )
            except asyncio.TimeoutError:
                self.etat = EtatSession.ECHEC
                self.derniere_erreur = "Délai d'ouverture dépassé."
                self.message = (
                    "Échec : le serveur n'a pas répondu dans le délai imparti."
                )
            except Exception as exc:  # réseau, DNS, TLS, refus…
                self.etat = EtatSession.ECHEC
                self.derniere_erreur = f"{type(exc).__name__}: {str(exc)[:180]}"
                self.message = (
                    "Échec de la connexion. Endpoint probablement à adapter "
                    "(variable WS_URL) : Pocket Option ne publie pas son protocole."
                )
            return self.instantane()
