"""Sonde de diagnostic : comprendre le protocole sans jamais révéler le jeton.

Objectif : ouvrir le WebSocket avec des en-têtes de navigateur (sinon le
serveur coupe net — c'est probablement ce que votre « échéance dépassée »
signifie), envoyer le SSID tel quel s'il est déjà un paquet Socket.IO, et
relever ce qui revient pendant quelques secondes.

Garanties :
    - aucun payload ne quitte cette fonction : uniquement type de trame,
      taille et premier caractère structurel (jamais assez pour identifier) ;
    - aucun ordre, aucun appel réseau autre que le handshake + l'écoute ;
    - aucun jeton écrit sur disque ;
    - la fonction est défensive : tout échec est rapporté, jamais masqué.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None

URL_MARCHE = (
    "wss://api-{region}.po.market/socket.io/?EIO=4&transport=websocket"
)
REGIONS = ("eu", "spb", "msk", "us-south")

ORIGIN_NAVIGATEUR = "https://pocketoption.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
)


@dataclass
class RapportRegion:
    region: str
    ouverture: str = ""       # "ok" | "erreur: <détail court>" | ""
    ssid_envoye: bool = False
    envoi_ssid: str = ""      # "tel_quel" | "enveloppe" | "erreur"
    trames: list[dict] = field(default_factory=list)
    mur_s: float = 0.0


def _resume_trame(brut) -> dict:
    """Ne garde que la forme de la trame — jamais son contenu."""
    if isinstance(brut, (bytes, bytearray)):
        texte = brut.decode("utf-8", errors="replace")
        type_texte = "binary"
    else:
        texte = str(brut)
        type_texte = "text"
    # Le premier caractère annonce le type Engine.IO/Socket.IO.
    t = texte.strip()[:1] or "?"
    resume = {"type": type_texte, "sid_io": t, "octets": len(texte)}
    # Nom de l'événement si la trame commence par 42["..."] : on ne sort
    # aucun contenu, seulement le nom déclaré de l'événement.
    if texte.startswith('42["'):
        fin = texte.find('",', 3)
        if fin == -1:
            fin = texte.find('"', 4)
        if 3 < fin < 40:
            evenement = texte[4:fin]
            if all(c.isalnum() or c in "./_ -" for c in evenement):
                resume["evenement"] = evenement
    return resume


async def sonder_une_region(ssid: str, region: str, ecoute_s: float = 6.0) -> RapportRegion:
    rapport = RapportRegion(region=region)
    if websockets is None:
        rapport.ouverture = "erreur: websockets introuvable"
        return rapport

    url = URL_MARCHE.format(region=region)
    debut = time.monotonic()
    try:
        async with websockets.connect(
            url,
            origin=ORIGIN_NAVIGATEUR,
            user_agent_header=USER_AGENT,
            additional_headers={
                "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            },
            open_timeout=8,
            close_timeout=3,
            ping_interval=None,  # nous pilotons nous-mêmes les ping
        ) as ws:
            rapport.ouverture = "ok"
            # Envoi du SSID (tel quel, s'il s'agit déjà d'un paquet Socket.IO)
            try:
                if ssid[:1].isdigit():
                    await ws.send(ssid)
                    rapport.envoi_ssid = "tel_quel"
                else:
                    # Enveloppe minimale : 42["auth", <ssid brut>]
                    await ws.send(f'42["auth",{json.dumps(ssid)}]')
                    rapport.envoi_ssid = "enveloppe"
                rapport.ssid_envoye = True
            except Exception as exc:
                rapport.envoi_ssid = f"erreur: {type(exc).__name__}"
                return rapport

            # Écoute courte et bornée — jamais un flux de données ouvert.
            fin = time.monotonic() + ecoute_s
            while time.monotonic() < fin:
                try:
                    brut = await asyncio.wait_for(
                        ws.recv(), timeout=max(0.5, fin - time.monotonic())
                    )
                except asyncio.TimeoutError:
                    break
                rapport.trames.append(_resume_trame(brut))
                if len(rapport.trames) >= 24:
                    break
    except Exception as exc:
        rapport.ouverture = f"erreur: {type(exc).__name__}: {str(exc)[:80]}"

    rapport.mur_s = round(time.monotonic() - debut, 2)
    return rapport


async def sonder(ssid: str, regions: tuple[str, ...] = REGIONS) -> list[dict]:
    resultats = []
    for region in regions:
        r = await sonder_une_region(ssid, region)
        resultats.append(
            {
                "region": r.region,
                "ouverture": r.ouverture,
                "envoi_ssid": r.envoi_ssid,
                "ssid_envoye": r.ssid_envoye,
                "trames": r.trames,
                "mur_s": r.mur_s,
            }
        )
    return resultats
