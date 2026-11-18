"""Diagnostic en lecture seule du flux WebSocket de la plateforme.

Ce script N'A PAS besoin de votre SSID : il ouvre la socket.io comme le ferait
un navigateur et affiche ce que le serveur envoie. Il ne fait aucun ordre, ne
se connecte à aucun compte, n'écrit rien sur disque.

Usage :
    python scripts/diagnostic_ws.py            # sonde 20 secondes
    python scripts/diagnostic_ws.py --duree 40
    python scripts/diagnostic_ws.py --auth    # tente des payloads d'auth FICTIFS

Le mode --auth n'utilise PAS votre SSID : il envoie des jetons fictifs pour
observer ce que le serveur répond (indice sur le format attendu).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import websockets

URL_PAR_DEFAUT = "wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket"
URL = URL_PAR_DEFAUT


async def sonder(duree: float) -> None:
    print("=" * 72)
    print("Diagnostic Pocket Option — en lecture seule, sans SSID")
    print("URL :", URL)
    print("=" * 72, flush=True)

    try:
        async with websockets.connect(
            URL,
            open_timeout=10,
            close_timeout=3,
            ping_interval=None,  # on gère le ping socket.io nous-même
            max_queue=64,
        ) as ws:
            print("\n[1] Socket TCP + handshake TLS établis.", flush=True)

            # a) Première trame : handshake socket.io
            try:
                brut = await asyncio.wait_for(ws.recv(), timeout=6)
                print("[2] Trame initiale :", _tronque(brut), flush=True)
            except asyncio.TimeoutError:
                print("[2] Aucune trame initiale en 6 s — serveur muet.", flush=True)
                return

            # b) Ouverture du namespace par défaut
            print("\n[3] Envoi de '40' (ouverture du namespace socket.io)…", flush=True)
            await ws.send("40")
            try:
                brut = await asyncio.wait_for(ws.recv(), timeout=6)
                print("[4] Réponse :", _tronque(brut), flush=True)
            except asyncio.TimeoutError:
                print("[4] Aucune réponse à '40' en 6 s.", flush=True)

            # c) On écoute ce que le serveur pousse ensuite
            print(f"\n[5] Écoute pendant {duree:.0f} s (contenu tronqué)…\n", flush=True)
            fin = asyncio.get_running_loop().time() + duree
            compteur = {}
            while asyncio.get_running_loop().time() < fin:
                try:
                    brut = await asyncio.wait_for(
                        ws.recv(), timeout=min(3.0, duree)
                    )
                except asyncio.TimeoutError:
                    continue
                except websockets.ConnectionClosed as exc:
                    print(f"    [connexion fermée par le serveur : {exc.code}]", flush=True)
                    break
                texte = brut.decode("utf-8", "replace") if isinstance(brut, (bytes, bytearray)) else brut
                cle = texte[:12]
                compteur[cle] = compteur.get(cle, 0) + 1
                apercu = _tronque(texte, 200)
                print(f"    {apercu}", flush=True)

            print("\n[6] Résumé des trames reçues :")
            for cle, n in sorted(compteur.items(), key=lambda x: -x[1]):
                print(f"    {n:5d}×  {cle}…")
            if not compteur:
                print("    (rien)")
    except asyncio.TimeoutError:
        print("Échec : le serveur n'a pas répondu au niveau TLS/TCP.", flush=True)
    except Exception as exc:
        print(f"Échec : {type(exc).__name__} : {exc}", flush=True)

    print("\nDiagnostic terminé. Rien n'a été écrit, aucun compte n'a été touché.", flush=True)


PAYLOADS_FICTIFS = [
    # Hypothèses raisonnables sur le format d'authentification socket.io.
    # Aucune ne contient de jeton réel ; le serveur rejettera, et sa réponse
    # nous renseigne sur le format attendu.
    ('42["authorization",{"session":"DUMMY_TOKEN_POCKET_LAB","isDemo":1}]', "authorization + session"),
    ('42["auth",{"ssid":"DUMMY_TOKEN_POCKET_LAB","isDemo":1}]', "auth + ssid"),
    ('42["authenticate",{"ssid":"DUMMY_TOKEN_POCKET_LAB"}]', "authenticate + ssid"),
    ('42["authorization","DUMMY_TOKEN_POCKET_LAB"]', "authorization brut"),
    ('42["ps",["EURUSD"]]', "souscription prix"),
]


async def sonder_auth() -> None:
    print("=" * 72)
    print("Sonde d'authentification (jetons fictifs) — lecture seule")
    print("URL :", URL)
    print("=" * 72, flush=True)

    for payload, nom in PAYLOADS_FICTIFS:
        print(f"\n>>> Tentative : {nom}", flush=True)
        try:
            async with websockets.connect(
                URL,
                open_timeout=10,
                close_timeout=3,
                ping_interval=None,
                max_queue=64,
            ) as ws:
                await ws.recv()  # handshake socket.io
                await ws.send("40")
                try:
                    await asyncio.wait_for(ws.recv(), timeout=5)  # 40{"sid":...}
                except asyncio.TimeoutError:
                    pass
                await ws.send(payload)
                for _ in range(4):
                    try:
                        brut = await asyncio.wait_for(ws.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        print("    (aucune réponse en 5 s)", flush=True)
                        break
                    except websockets.ConnectionClosed as exc:
                        print(f"    [serveur ferme la connexion : code {exc.code}]", flush=True)
                        break
                    texte = (
                        brut.decode("utf-8", "replace")
                        if isinstance(brut, (bytes, bytearray))
                        else brut
                    )
                    print("    ←", _tronque(texte, 400), flush=True)
        except Exception as exc:
            print(f"    Échec : {type(exc).__name__} : {exc}", flush=True)

    print("\nSonde terminée. Aucun compte n'a été touché (jetons fictifs).", flush=True)


def _tronque(texte: str | bytes, limite: int = 300) -> str:
    if isinstance(texte, bytes):
        texte = texte.decode("utf-8", "replace")
    texte = texte.replace("\n", " ")
    if len(texte) > limite:
        return texte[: limite - 3] + "..."
    return texte


def main() -> int:
    for flux in (sys.stdout, sys.stderr):
        try:
            if (flux.encoding or "").lower().replace("-", "") != "utf8":
                flux.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
        except (AttributeError, ValueError):
            pass
    parseur = argparse.ArgumentParser()
    parseur.add_argument("--duree", type=float, default=20.0)
    parseur.add_argument(
        "--auth",
        action="store_true",
        help="tente des payloads d'authentification FICTIFS et affiche les réponses",
    )
    parseur.add_argument(
        "--url",
        default=URL_PAR_DEFAUT,
        help="endpoint WebSocket à sonder (défaut : endpoint démo fourni par le client)",
    )
    args = parseur.parse_args()
    global URL
    URL = args.url
    if args.auth:
        asyncio.run(sonder_auth())
    else:
        asyncio.run(sonder(args.duree))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
