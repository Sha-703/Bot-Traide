"""Serveur de la console de supervision (FastAPI).

Le dashboard est un simple observateur : il lit un état déjà calculé par le
moteur (`Moteur.instantane()`) et ne reçoit jamais le SSID ni aucun secret.

Endpoints :
    GET  /                  -> console HTML
    GET  /api/etat          -> instantané JSON (fallback polling)
    GET  /api/stream        -> flux SSE temps réel
    GET  /api/trades.csv    -> export du journal
    POST /api/controle      -> pause / reprise / redémarrage
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .engine import Moteur, Statut
from .navigateur import (
    HOTES_DIAGNOSTIC,
    HOTE_LOGIN,
    ConnexionEchouee,
    diagnostic_reseau,
    lancer_connexion,
    proxy_configure,
)
from .reglages import ReglageInvalide, Reglages
from .session import GestionnaireSession, SessionInvalide
from .sonde import sonder
from .mt5_adapter import diagnostiquer_mt5
from .verrou import VerrouInstance

WEB = Path(__file__).resolve().parent / "web"
log = logging.getLogger("pocketlab")


def creer_application(settings: Settings, moteur: Moteur | None = None) -> FastAPI:
    """Construit l'application FastAPI autour d'un moteur de trading."""
    moteur = moteur or Moteur(settings)

    @asynccontextmanager
    async def cycle_de_vie(app: FastAPI):
        # Un seul moteur par dossier de journal : deux instances écriraient
        # dans le même CSV et fausseraient compteurs et statistiques.
        verrou = VerrouInstance(settings.dossier_journal)
        verrou.prendre()
        # Le mode réel n'a aucun adaptateur d'exécution : on ne démarre pas
        # la boucle automatiquement, pour éviter toute surprise.
        if moteur.mode == "REEL":
            moteur.alerte(
                "avertissement",
                "MODE_REEL détecté : exécution réelle verrouillée, "
                "aucun ordre ne sera transmis. Consultez SECURITY.md.",
            )
        else:
            moteur.demarrer_en_tache()
        # Le solde affiché doit refléter le compte dès le premier affichage,
        # pas seulement après un basculement de mode.
        await moteur.rafraichir_solde()
        try:
            yield
        finally:
            await moteur.arreter()
            moteur.desabonner_tous()
            verrou.liberer()

    app = FastAPI(
        title="Pocket Lab — Console de trading",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=cycle_de_vie,
    )
    app.state.moteur = moteur
    app.state.settings = settings
    # Session plateforme : en mémoire uniquement, effacée à l'arrêt.
    # Si un SSID existe dans `.env`, il sert de valeur de départ (jamais logué).
    app.state.session = GestionnaireSession()
    if settings.ssid:
        try:
            app.state.session.enregistrer(settings.ssid)
        except SessionInvalide:
            log.warning("SSID du .env ignoré : format invalide (trop court ?).")
    # Verrou : une seule tentative de connexion navigateur à la fois.
    app.state.login_en_cours = False

    if WEB.exists():
        app.mount("/static", StaticFiles(directory=str(WEB)), name="static")

    # Console locale : le navigateur ne doit jamais garder une vieille copie
    # de l'interface. Sans cela, un onglet resté ouvert affiche l'ancien HTML
    # alors que le serveur, lui, est à jour — et les nouveaux boutons semblent
    # absents.
    @app.middleware("http")
    async def pas_de_cache_statique(request, call_next):
        reponse = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            reponse.headers["Cache-Control"] = "no-store, max-age=0"
        return reponse

    # ------------------------------------------------------------------ #
    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

    @app.get("/api/etat")
    async def etat() -> JSONResponse:
        return JSONResponse(moteur.instantane())

    @app.get("/api/stream")
    async def stream(request: Request) -> StreamingResponse:
        """Server-Sent Events : un instantané poussé à chaque mise à jour."""
        file = moteur.abonner()

        async def generer():
            try:
                yield f"data: {json.dumps(moteur.instantane())}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        etat = await asyncio.wait_for(file.get(), timeout=15)
                        yield f"data: {json.dumps(etat)}\n\n"
                    except asyncio.TimeoutError:
                        # Heartbeat : garde la connexion ouverte derrière un proxy.
                        yield ": keep-alive\n\n"
            finally:
                moteur.desabonner(file)

        return StreamingResponse(
            generer(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.get("/api/trades.csv")
    async def exporter() -> PlainTextResponse:
        chemin = moteur.journal.chemin_trades
        try:
            contenu = chemin.read_text(encoding="utf-8")
        except FileNotFoundError:
            contenu = ""
        return PlainTextResponse(
            contenu,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="trades.csv"'},
        )

    @app.post("/api/controle")
    async def controle(charge: dict) -> JSONResponse:
        action = str(charge.get("action", "")).lower()
        if action == "pause":
            moteur.mettre_en_pause()
        elif action in ("reprendre", "demarrer"):
            # « Reprendre » suppose une tâche vivante (suspendue). Après un
            # arrêt ou une réinitialisation, il faut recréer la boucle.
            if moteur.tache_active:
                moteur.reprendre()
            else:
                moteur.demarrer_en_tache()
        elif action == "arreter":
            await moteur.arreter()
        else:
            raise HTTPException(status_code=400, detail=f"Action inconnue : {action}")
        return JSONResponse({"statut": moteur.statut.value})

    # ------------------------------------------------------------------ #
    #  Campagne : archiver l'historique et repartir de zéro
    # ------------------------------------------------------------------ #
    @app.post("/api/campagne")
    async def nouvelle_campagne() -> JSONResponse:
        return JSONResponse(await moteur.nouvelle_campagne())

    @app.post("/api/reinitialiser")
    async def reinitialiser() -> JSONResponse:
        return JSONResponse(await moteur.reinitialiser_tout(app.state.session))

    # ------------------------------------------------------------------ #
    #  Réglages (mise, garde-fous, mode) — appliqués à chaud
    # ------------------------------------------------------------------ #
    @app.get("/api/reglages")
    async def lire_reglages() -> JSONResponse:
        return JSONResponse(moteur.reglages.instantane())

    @app.get("/api/reglages/defauts")
    async def reglages_par_defaut() -> JSONResponse:
        """Valeurs issues du `.env` (le bouton « Valeurs du .env »)."""
        return JSONResponse(Reglages.depuis_settings(settings).instantane())

    @app.put("/api/reglages")
    async def maj_reglages(charge: dict) -> JSONResponse:
        try:
            nouveaux = moteur.reglages.mis_a_jour(charge)
        except ReglageInvalide as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        changement_mode = nouveaux.mode != moteur.mode
        changement_flux = nouveaux.flux_prix != moteur.reglages.flux_prix
        if changement_mode:
            if nouveaux.mode == "REEL":
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Mode réel verrouillé : aucun adaptateur d'exécution n'est "
                        "livré avec ce projet. Utilisez le mode Observation "
                        "(données réelles, ordres papier) ou écrivez puis auditez "
                        "un courtier dédié — voir SECURITY.md."
                    ),
                )
            if nouveaux.mode == "OBSERVATION" and not app.state.session.configuree:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Connectez d'abord votre session (SSID) depuis le panneau "
                        "Connexion, puis activez le mode Observation."
                    ),
                )

        moteur.appliquer_reglages(nouveaux)
        message = "Réglages appliqués."
        if changement_mode:
            await moteur.basculer_mode(nouveaux.mode, app.state.session)
            message = f"Mode {nouveaux.mode} activé."
        elif changement_flux:
            await moteur.recharger_flux(app.state.session)
            message = (
                f"Flux de prix : {'Binance (prix réels)' if nouveaux.flux_prix == 'binance' else 'simulation'} "
                f"· {moteur.actif}."
            )
            if moteur.statut.value == "EN_PAUSE":
                message += (
                    " Bot en pause : quota ou perte maximale atteint — "
                    "ajustez les limites, ou utilisez scripts/mesurer_flux_reel.py "
                    "pour mesurer sans quota."
                )
            elif moteur.statut.value != "EN_COURS":
                message += " Cliquez sur Démarrer pour lancer la collecte."
        return JSONResponse(
            {"message": message, "reglages": moteur.reglages.instantane()}
        )

    # ------------------------------------------------------------------ #
    #  Session plateforme (SSID en mémoire, jamais persisté)
    # ------------------------------------------------------------------ #
    @app.get("/api/session")
    async def lire_session() -> JSONResponse:
        return JSONResponse(app.state.session.instantane())

    @app.post("/api/session")
    async def enregistrer_session(charge: dict) -> JSONResponse:
        try:
            etat = app.state.session.enregistrer(str(charge.get("ssid") or ""))
        except SessionInvalide as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if moteur.mode == "OBSERVATION":
            # Nouveau jeton : on reconnecte le flux avec celui-ci.
            await moteur.basculer_mode("OBSERVATION", app.state.session)
        return JSONResponse(etat)

    @app.delete("/api/session")
    async def effacer_session() -> JSONResponse:
        etat = app.state.session.oublier()
        if moteur.mode == "OBSERVATION":
            # Plus de session : retour au flux simulé, sans laisser croire
            # que des données de plateforme arrivent encore.
            await moteur.basculer_mode("DEMO", None)
        return JSONResponse(etat)

    @app.post("/api/session/tester")
    async def tester_session() -> JSONResponse:
        try:
            etat = await app.state.session.tester()
        except SessionInvalide as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if moteur.mode == "OBSERVATION":
            await moteur.rafraichir_solde(app.state.session)
        return JSONResponse(etat)

    @app.post("/api/session/sonder")
    async def sonder_session() -> JSONResponse:
        """Diagnostic détaillé : pourquoi le serveur répond ou pas.

        N'expose aucun secret : uniquement la région contactée, le type de
        trames réseau et le nom déclaré des événements reçus. Aucun payload
        n'est renvoyé, jamais enregistré.
        """
        if not app.state.session.configuree:
            raise HTTPException(
                status_code=400,
                detail="Aucun SSID en mémoire : connectez-vous d'abord.",
            )
        regions = await sonder(app.state.session.ssid)
        return JSONResponse({"regions": regions})

    @app.get("/api/mt5")
    async def etat_mt5() -> JSONResponse:
        """État du terminal MT5 et du compte démo. Aucun secret."""
        rapport = await asyncio.to_thread(
            diagnostiquer_mt5,
            settings.mt5_chemin_terminal,
            settings.mt5_symbole,
            settings.mt5_demo,
        )
        return JSONResponse(rapport)

    @app.get("/api/reseau")
    async def etat_reseau() -> JSONResponse:
        """Sonde les hôtes de la plateforme (lecture seule, sans compte)."""
        resultats = await asyncio.to_thread(diagnostic_reseau)
        site = next(
            (h["joignable"] for h in resultats if h["hote"] == HOTE_LOGIN), False
        )
        return JSONResponse(
            {
                "verifie_le": time.strftime("%H:%M:%S"),
                "proxy_configure": proxy_configure() is not None,
                "site_joignable": site,
                "hotes": resultats,
                "hotes_attendus": len(HOTES_DIAGNOSTIC),
            }
        )

    @app.post("/api/session/navigateur")
    async def connexion_navigateur() -> JSONResponse:
        """Ouvre Edge ; l'utilisateur se connecte lui-même, nous lisons le cookie."""
        if app.state.login_en_cours:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Une fenêtre de connexion est déjà ouverte. "
                    "Connectez-vous dans cette fenêtre, ou attendez qu'elle soit "
                    "fermée avant de réessayer."
                ),
            )
        reste = getattr(app.state, "login_bloque_jusqua", 0.0) - time.time()
        if reste > 0:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Tentative précédente échouée : patientez {int(reste) + 1} s. "
                    "Le site limite les connexions répétées ; insister aggrave "
                    "le blocage."
                ),
                headers={"Retry-After": str(int(reste) + 1)},
            )
        app.state.login_en_cours = True
        try:
            try:
                resultat = await asyncio.to_thread(lancer_connexion)
            except ConnexionEchouee as exc:
                # Pause imposée : protège l'accès (et le compte) du martèlement.
                app.state.login_bloque_jusqua = time.time() + 120
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            try:
                etat = app.state.session.enregistrer(
                    resultat.ssid, origine="navigateur"
                )
            except SessionInvalide as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Format de session inattendu : {exc}",
                ) from exc
            # Jamais la valeur du jeton dans les logs : uniquement le masque.
            log.info(
                "Connexion navigateur établie (%d trames capturées · aperçu %s).",
                resultat.trames_capturees,
                etat["apercu"],
            )
            if moteur.mode == "OBSERVATION":
                await moteur.basculer_mode("OBSERVATION", app.state.session)
            return JSONResponse(
                {
                    **etat,
                    "trames_capturees": resultat.trames_capturees,
                    "capture_disponible": bool(resultat.capture),
                }
            )
        finally:
            app.state.login_en_cours = False

    @app.get("/api/sante")
    async def sante() -> dict:
        return {
            "ok": True,
            "mode": moteur.mode,
            "statut": moteur.statut.value if hasattr(moteur, "statut") else Statut.ARRETE.value,
            "uptime_s": moteur.instantane()["uptime_s"],
            "session": app.state.session.configuree,
        }

    return app


def _port_disponible(hote: str, port: int) -> bool:
    """Vrai si le port peut être réservé (test bind immédiat, sans écoute)."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sonde:
        sonde.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sonde.bind((hote, port))
            return True
        except OSError:
            return False


def choisir_port(hote: str, port_souhaite: int) -> int:
    """Trouve un port libre : évite le classique « address already in use »."""
    if _port_disponible(hote, port_souhaite):
        return port_souhaite
    for candidat in range(port_souhaite + 1, port_souhaite + 21):
        if _port_disponible(hote, candidat):
            return candidat
    raise RuntimeError(f"Aucun port libre entre {port_souhaite} et {port_souhaite + 20}.")


def lancer(settings: Settings) -> None:
    """Point d'entrée utilisé par `main.py`."""
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    port = choisir_port(settings.hote_dashboard, settings.port_dashboard)
    if port != settings.port_dashboard:
        log.warning(
            "Port %s déjà utilisé : la console bascule sur %s.",
            settings.port_dashboard,
            port,
        )
    url = f"http://{settings.hote_dashboard}:{port}"
    log.info("Console prête : %s  (mode %s)", url, settings.mode)
    log.info(
        "Configuration : actif=%s · tick=%.2fs · expiration=%ss · quota=%s/jour · "
        "perte max=%s$ · vitesse x%.0f",
        settings.actif,
        settings.intervalle_tick_s / max(settings.facteur_vitesse, 0.01),
        settings.duree_expiration_s,
        settings.trades_max_par_jour,
        settings.perte_max_journaliere,
        settings.facteur_vitesse,
    )
    print(f"\n  ➜  Console Pocket Lab : {url}\n      (Ctrl+C pour arrêter)\n", flush=True)

    # Ouverture automatique du navigateur quand on passe par demarrer.cmd.
    if (os.getenv("OUVRIR_NAVIGATEUR") or "0").strip().lower() in {"1", "true", "oui", "yes"}:
        def _ouvrir() -> None:
            time.sleep(2.0)
            try:
                webbrowser.open(url)
            except Exception:  # pas de navigateur par défaut : sans gravité
                pass

        threading.Thread(target=_ouvrir, daemon=True).start()

    uvicorn.run(
        creer_application(settings),
        host=settings.hote_dashboard,
        port=port,
        log_level="warning",
    )
    print("\n  Console arrêtée.\n", flush=True)
