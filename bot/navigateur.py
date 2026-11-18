"""Connexion supervisée via votre navigateur (Microsoft Edge).

Le principe — identique aux flux OAuth :

    1. la console ouvre une fenêtre Edge normale sur la page de connexion ;
    2. VOUS tapez votre email, votre mot de passe et le captcha — le bot ne
       voit et n'enregistre jamais ces informations ;
    3. dès que la session apparaît dans les cookies du navigateur, le jeton
       `po_session` est lu et placé en mémoire vive (jamais sur disque) — la
       navigation d'après-connexion peut échouer sans invalider la session ;
    4. les trames WebSocket échangées par votre propre navigateur sont
       capturées localement (`.audit/`) pour permettre la reproduction du
       protocole ; elles contiennent votre jeton : à considérer comme un mot
       de passe, à supprimer ensuite.

Ce module n'automatise AUCUNE saisie : la page de connexion reste pilotée
par l'humain.
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

URL_SITE = "https://pocketoption.com"
URL_LOGIN = URL_SITE + "/en/login/"
HOTE_LOGIN = "pocketoption.com"

# Hôtes utiles au diagnostic : le site (connexion) et les serveurs de cotation.
HOTES_DIAGNOSTIC: tuple[tuple[str, int, str], ...] = (
    ("pocketoption.com", 443, "Site — page de connexion"),
    ("api-eu.po.market", 443, "API temps réel"),
    ("demo-api-eu.po.market", 443, "API compte démo"),
    ("events-po.com", 443, "Flux d'événements"),
)

# Délai laissé à l'utilisateur pour saisir ses identifiants (3 minutes).
DELAI_CONNEXION_S = 300.0
# Le site est parfois très lent : on laisse plus de temps et on réessaie.
DELAI_GOTO_MS = 90_000
TENTATIVES_GOTO = 3
# Fenêtre d'écoute du WebSocket après la redirection de succès.
DUREE_ECOUTE_S = 10.0

DOSSIER_CAPTURE = Path(__file__).resolve().parent.parent / ".audit"


class ConnexionEchouee(RuntimeError):
    """La connexion via le navigateur n'a pas pu être établie."""


@dataclass
class ResultatConnexion:
    # Le jeton ne quitte jamais la mémoire du serveur (jamais sérialisé,
    # jamais journalisé). Il sert uniquement à alimenter la session.
    ssid: str = field(repr=False)
    capture: str = ""      # chemin local du fichier de trames (analyse)
    trames_capturees: int = 0


def proxy_configure() -> dict[str, str] | None:
    """Proxy optionnel pour la fenêtre de connexion (`PROXY_NAVIGATEUR`)."""
    valeur = (os.getenv("PROXY_NAVIGATEUR") or "").strip()
    return {"server": valeur} if valeur else None


def verifier_reseau(hote: str = HOTE_LOGIN, port: int = 443, delai: float = 8.0) -> str | None:
    """Teste l'accessibilité TCP du site. Retourne None si joignable, sinon le motif.

    À appeler AVANT d'ouvrir une fenêtre : sans cela, l'utilisateur attend
    une minute pour découvrir que son réseau bloque le site.
    """
    try:
        with socket.create_connection((hote, port), timeout=delai):
            return None
    except OSError as exc:
        return f"{type(exc).__name__} : {exc}"


def _sonder(hote: str, port: int, delai: float = 4.0) -> dict[str, Any]:
    """Sonde un hôte en TCP (aucune donnée envoyée, aucune authentification)."""
    adresse = ""
    try:
        adresse = socket.getaddrinfo(hote, port, type=socket.SOCK_STREAM)[0][4][0]
    except OSError as exc:
        return {
            "hote": hote,
            "port": port,
            "adresse": "",
            "joignable": False,
            "motif": f"DNS : {type(exc).__name__}",
            "ms": 0,
        }
    debut = time.monotonic()
    try:
        with socket.create_connection((hote, port), timeout=delai):
            return {
                "hote": hote,
                "port": port,
                "adresse": adresse,
                "joignable": True,
                "motif": "",
                "ms": int((time.monotonic() - debut) * 1000),
            }
    except OSError as exc:
        return {
            "hote": hote,
            "port": port,
            "adresse": adresse,
            "joignable": False,
            "motif": type(exc).__name__,
            "ms": int((time.monotonic() - debut) * 1000),
        }


def diagnostic_reseau(delai: float = 4.0) -> list[dict[str, Any]]:
    """Sonde les hôtes de la plateforme en parallèle (lecture seule, sans compte)."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=len(HOTES_DIAGNOSTIC)) as pool:
        resultats = list(
            pool.map(lambda cible: _sonder(cible[0], cible[1], delai), HOTES_DIAGNOSTIC)
        )
    for resultat, (_, _, libelle) in zip(resultats, HOTES_DIAGNOSTIC):
        resultat["libelle"] = libelle
    return resultats


def lancer_connexion(
    delai: float = DELAI_CONNEXION_S,
    url_login: str = URL_LOGIN,
) -> ResultatConnexion:
    """Ouvre Edge, attend la connexion de l'utilisateur, retourne la session.

    Le navigateur est refermé dès que le cookie est lu. Ne retourne jamais le
    jeton en clair en dehors du résultat (qui n'est utilisé qu'en mémoire).
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    DOSSIER_CAPTURE.mkdir(parents=True, exist_ok=True)

    # Pas de blocage sur une sonde unique : la liaison au site est instable
    # (elle répond parfois en 250 ms, parfois pas du tout). On tente la vraie
    # navigation, plusieurs fois, et on ne conclut qu'après échec réel.
    trames: list[dict[str, Any]] = []
    try:
        with sync_playwright() as pw:
            navigateur = None
            try:
                navigateur = pw.chromium.launch(
                    channel="msedge",
                    headless=False,
                    proxy=proxy_configure(),
                    # Limite les indices « automatisé » sans y toucher autrement :
                    # on ne falsifie rien, on réutilise simplement Edge.
                    ignore_default_args=["--enable-automation"],
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except PlaywrightError as exc:
                raise ConnexionEchouee(
                    "Edge n'a pas pu être piloté par Playwright. "
                    "Vérifiez que Microsoft Edge est installé. "
                    f"Détail : {exc}"
                ) from exc

            contexte = navigateur.new_context(
                viewport={"width": 1280, "height": 800},
                locale="fr-FR",
                timezone_id="Europe/Paris",
            )
            page = contexte.new_page()

            # --- Capture des trames WebSocket (CDP) ----------------------
            try:
                cdp = contexte.new_cdp_session(page)
                cdp.send("Network.enable")

                def _capturer(params: dict[str, Any], *, direction: str) -> None:
                    trame = params.get("frame", {}) or {}
                    trames.append(
                        {
                            "direction": direction,
                            "opcode": trame.get("opcode"),
                            "payload": trame.get("payloadData"),
                        }
                    )

                cdp.on(
                    "Network.webSocketFrameSent",
                    lambda params: _capturer(params, direction="sent"),
                )
                cdp.on(
                    "Network.webSocketFrameReceived",
                    lambda params: _capturer(params, direction="received"),
                )
            except PlaywrightError:
                cdp = None  # capture indisponible : la connexion fonctionne quand même

            derniere_erreur = ""
            for tentative in range(1, TENTATIVES_GOTO + 1):
                try:
                    page.goto(
                        url_login,
                        wait_until="domcontentloaded",
                        timeout=DELAI_GOTO_MS,
                    )
                    derniere_erreur = ""
                    break
                except PlaywrightTimeout:
                    derniere_erreur = "délai dépassé"
                except PlaywrightError as exc:
                    derniere_erreur = str(exc).strip().splitlines()[0][:160]
                if tentative < TENTATIVES_GOTO:
                    time.sleep(2.5)

            if derniere_erreur:
                _fermer(navigateur)
                raise ConnexionEchouee(
                    "Le site pocketoption.com ne répond pas depuis ce réseau "
                    f"({derniere_erreur}) après {TENTATIVES_GOTO} tentatives. "
                    "La liaison est instable ou filtrée : réessayez dans "
                    "quelques minutes, activez un VPN/proxy sur le poste, ou "
                    "collez le jeton d'une session ouverte depuis un appareil "
                    "qui accède au site. Les serveurs de cotation po.market "
                    "restent souvent joignables même quand le site ne l'est pas."
                )

            # --- Attente de la connexion (sans dépendre du chargement) ----
            # Après le login, le site redirige vers un autre domaine. Cette
            # navigation peut échouer (ERR_CONNECTION_TIMED_OUT, réseau filtré,
            # proxy…) SANS invalider la session déjà créée. On surveille donc
            # le COOKIE de session, jamais le chargement de la page.
            def _jeton() -> str | None:
                try:
                    cookies = contexte.cookies()
                except PlaywrightError:
                    return None
                exact = next(
                    (
                        c
                        for c in cookies
                        if c.get("name") == "po_session" and c.get("value")
                    ),
                    None,
                )
                if exact:
                    return exact["value"]
                # Cookie fragmenté (po_session_1, po_session_2…) : on recolle.
                morceaux = [
                    c
                    for c in cookies
                    if str(c.get("name", "")).startswith("po_session")
                    and c.get("value")
                ]
                if morceaux:
                    return "".join(c["value"] for c in morceaux)
                return None

            jeton_initial = _jeton()  # possible cookie anonyme posé au chargement
            debut = time.monotonic()
            derniere_url = ""
            while time.monotonic() - debut < delai:
                if page.is_closed():
                    jeton = _jeton()  # fenêtre fermée juste après le login
                    if jeton and jeton != jeton_initial:
                        break
                    _fermer(navigateur)
                    raise ConnexionEchouee(
                        "Fenêtre fermée avant la fin de la connexion : "
                        "rien n'a été enregistré."
                    )
                try:
                    derniere_url = page.url
                except PlaywrightError:
                    derniere_url = derniere_url or "?"
                jeton = _jeton()
                if jeton and (jeton != jeton_initial or "/login/" not in derniere_url):
                    break
                time.sleep(0.4)
            else:
                _fermer(navigateur)
                raise ConnexionEchouee(
                    f"Délai de {delai / 60:.0f} min dépassé : connexion non "
                    f"détectée (dernière page : {derniere_url or 'inconnue'}). "
                    "Rien n'a été enregistré. Si la page d'après-connexion reste "
                    "bloquée (ERR_CONNECTION_TIMED_OUT), l'entrée manuelle du "
                    "jeton fonctionne même quand ce domaine est injoignable."
                )

            # Laisse à l'application le temps d'ouvrir son WebSocket et
            # d'envoyer les premières trames (auth, assets, solde…).
            # La page peut avoir été fermée ou bloquée : on ne l'exige pas.
            fin_ecoute = time.monotonic() + DUREE_ECOUTE_S
            while time.monotonic() < fin_ecoute and not page.is_closed():
                time.sleep(0.25)

            trames = trames[:500]  # borne de sécurité
            chemin_capture = _ecrire_capture(trames)

            ssid = _jeton() or jeton_initial
            _fermer(navigateur)

        if not ssid:
            raise ConnexionEchouee(
                "Connexion détectée mais cookie « po_session » introuvable."
            )
        return ResultatConnexion(
            ssid=ssid,
            capture=str(chemin_capture) if chemin_capture else "",
            trames_capturees=len(trames),
        )
    except ConnexionEchouee:
        raise
    except Exception as exc:  # Edge fermé à la main, crash, etc.
        detail = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else ""
        if "ERR_" in detail or "net::" in detail:
            message = (
                "Problème réseau pendant la connexion : le site n'a pas pu "
                "être atteint (domaine bloqué, DNS, proxy…)."
            )
        else:
            message = f"Connexion interrompue : {type(exc).__name__}."
        if detail:
            message += f" Détail : {detail}"
        raise ConnexionEchouee(message) from exc


def _fermer(navigateur: Any) -> None:
    """Ferme le navigateur sans jamais masquer l'erreur d'origine."""
    try:
        navigateur.close()
    except Exception:
        pass


def _ecrire_capture(trames: list[dict[str, Any]]) -> Path | None:
    """Écrit les trames (tronquées) pour reproduction du protocole."""
    if not trames:
        return None
    chemin = DOSSIER_CAPTURE / f"frames_capture_{int(time.time())}.jsonl"
    try:
        with chemin.open("w", encoding="utf-8") as f:
            for trame in trames:
                payload = trame.get("payload")
                if isinstance(payload, str) and len(payload) > 4000:
                    payload = payload[:4000] + "…"
                f.write(
                    json.dumps(
                        {
                            "dir": trame.get("direction"),
                            "opcode": trame.get("opcode"),
                            "payload": payload,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        return chemin
    except OSError:
        return None
