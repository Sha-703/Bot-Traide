"""Orchestrateur : le cœur qui relie flux -> stratégie -> risque -> exécution.

La boucle est volontairement séquentielle et lisible :
    tick -> stratégie -> (signal ?) -> risque -> ordre -> échéance -> résultat
         -> journal -> diffusion au dashboard

Un seul trade à la fois, une seule boucle, aucun thread caché : le
comportement reste reproductible et auditable.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .config import Settings
from .connexion import (
    ConnexionPocketOption,
    EtatConnexion,
    FluxBinance,
    FluxSynthetique,
    SourceDePrix,
    Tick,
)
from .execution import ExecutionRefusee, MoteurExecution, Ordre
from .journalisation import Journal
from .reglages import Reglages
from .mt5_adapter import SourceMT5, SoldeMT5Demo
from .risque import GestionRisque, Limites, RisqueRefuse
from .solde import construire_source_solde
from .strategie import StrategieBollinger, StrategieMoyennesMobiles


class Statut(str, Enum):
    ARRETE = "ARRETE"
    EN_COURS = "EN_COURS"
    EN_PAUSE = "EN_PAUSE"
    TERMINE = "TERMINE"
    ERREUR = "ERREUR"


@dataclass
class Alerte:
    niveau: str  # info | avertissement | erreur
    message: str
    ts: float = field(default_factory=time.time)


class Moteur:
    """Boucle de trading et état partagé avec l'interface web."""

    def __init__(self, settings: Settings, journal: Journal | None = None) -> None:
        self.settings = settings
        self.journal = journal or Journal(settings.dossier_journal)

        # Réglages modifiables à chaud depuis la console. Le moteur en garde
        # une seule instance : les mises à jour se font en place, donc toutes
        # les références (risque, exécution) restent valides.
        self.reglages = Reglages.depuis_settings(settings)

        if settings.strategie == "ema":
            self.strategie = StrategieMoyennesMobiles(
                ema_rapide=settings.ema_rapide,
                ema_lente=settings.ema_lente,
                seuil_ecart_bps=settings.seuil_ecart_bps,
                cooldown_secondes=settings.cooldown_signal_s,
            )
        elif settings.strategie == "bollinger":
            self.strategie = StrategieBollinger(
                periode_bougies=settings.bollinger_periode,
                k=settings.bollinger_k,
                cooldown_secondes=settings.cooldown_signal_s,
            )
        else:
            raise RuntimeError(
                f"STRATEGIE={settings.strategie!r} inconnue : "
                "attendu 'ema' ou 'bollinger'."
            )
        self.risque = GestionRisque(
            Limites(
                montant_max_position=self.reglages.taille_max_position,
                fraction_risque_par_trade=self.reglages.fraction_risque_par_trade,
                perte_max_journaliere=self.reglages.perte_max_journaliere,
                trades_max_par_jour=self.reglages.trades_max_par_jour,
            ),
            capital_initial=settings.capital_initial,
        )
        self.execution = MoteurExecution(
            settings, self.risque, self.journal, self.reglages
        )
        # Mode posé AVANT la création du flux : _construire_source en dépend.
        self.mode: str = self.reglages.mode
        self.execution.mode_reel = self.mode == "REEL"
        self.execution.mode_label = self.mode

        self.source: SourceDePrix = self._construire_source(None)
        self.execution.actif = getattr(self.source, "actif", settings.actif)
        self.solde_plateforme: float | None = None
        self.origine_solde: str = "simulé"
        self.note_solde: str = "Capital papier du simulateur."

        # --- État observable ------------------------------------------
        self.capital = settings.capital_initial
        self.statut = Statut.ARRETE
        self.dernier_tick: Tick | None = None
        self.dernier_signal: dict[str, Any] | None = None
        self.nb_ticks = 0
        self.nb_signaux = 0
        self.nb_refus = 0
        self.nb_trades_ouverts = 0  # cumul de la campagne (toutes journées)
        self.debut_ts = time.time()
        self.alertes: deque[Alerte] = deque(maxlen=40)
        self._abonnes: set[asyncio.Queue[dict[str, Any]]] = set()
        self._tache: asyncio.Task[None] | None = None
        self._pause = asyncio.Event()
        self._pause.set()  # set = la boucle tourne
        self._arret = asyncio.Event()
        self.max_trades: int | None = None  # limite optionnelle (campagne de test)
        self.max_ticks: int | None = None  # garde-fou anti-boucle-infinie
        self._veille: asyncio.Task[None] | None = None  # veille du flux réel

        # En dernier : l'historique du journal reprend le pas sur les valeurs
        # par défaut (capital, compteurs du jour).
        self._rehydrate_depuis_journal()

    # ------------------------------------------------------------------ #
    #  Diffusion d'état (SSE / dashboard)
    # ------------------------------------------------------------------ #
    def _rehydrate_depuis_journal(self) -> None:
        """Réaligne le capital et les compteurs du jour sur l'historique.

        Sans cela, un redémarrage remettrait le capital papier à
        `capital_initial` alors que le CSV contient déjà des trades gagnants
        ou perdants — la console indiquerait deux vérités différentes.
        """
        trades = self.journal.trades
        if not trades:
            return
        self.capital = trades[-1].solde_apres
        self.nb_trades_ouverts = len(trades)

        jour = self.risque.etat.jour
        etat = self.risque.etat
        pnl_jour = 0.0
        pertes = 0.0
        gains = 0.0
        n_jour = 0
        for trade in trades:
            if (trade.horodatage_utc or "")[:10] != jour:
                continue
            n_jour += 1
            pnl_jour += trade.pnl
            if trade.pnl >= 0:
                gains += trade.pnl
            else:
                pertes += abs(trade.pnl)
        etat.trades_du_jour = n_jour
        etat.pnl_du_jour = round(pnl_jour, 2)
        etat.pertes_du_jour = round(pertes, 2)
        etat.gains_du_jour = round(gains, 2)
        if trades:
            self.alerte(
                "info",
                f"Historique rechargé : {len(trades)} trade(s), "
                f"capital papier repris à {self.capital:.2f} $.",
            )

    def abonner(self) -> asyncio.Queue[dict[str, Any]]:
        file: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
        self._abonnes.add(file)
        return file

    def desabonner(self, file: asyncio.Queue[dict[str, Any]]) -> None:
        self._abonnes.discard(file)

    def desabonner_tous(self) -> None:
        """Ferme proprement tous les flux SSE (arrêt du serveur)."""
        self._abonnes.clear()

    def _diffuser(self) -> None:
        instantane = self.instantane()
        for file in list(self._abonnes):
            try:
                file.put_nowait(instantane)
            except asyncio.QueueFull:
                # Le client est lent : on préfère perdre une image à un blocage.
                pass

    def alerte(self, niveau: str, message: str) -> None:
        # Anti-spam : une même alerte répétée en boucle n'apprend rien.
        if self.alertes:
            derniere = self.alertes[-1]
            if derniere.message == message and time.time() - derniere.ts < 60:
                return
        self.alertes.append(Alerte(niveau, message))
        self.journal.evenement(niveau, "moteur", message)

    # ------------------------------------------------------------------ #
    #  Instantané pour l'UI
    # ------------------------------------------------------------------ #
    def instantane(self) -> dict[str, Any]:
        stats = self.journal.statistiques()
        ordre = self.execution.ordre_en_cours
        maintenant = time.time()

        # La configuration exposée reflète les réglages VIVANTS, pas seulement
        # le .env : l'utilisateur doit voir ce qui est réellement appliqué.
        config = dict(self.settings.resume_public())
        config.update(
            {
                "mode": self.mode,
                "montant_trade": round(self.reglages.montant_trade, 2),
                "taille_max_position": round(self.reglages.taille_max_position, 2),
                "fraction_risque_par_trade": self.reglages.fraction_risque_par_trade,
                "perte_max_journaliere": round(self.reglages.perte_max_journaliere, 2),
                "trades_max_par_jour": self.reglages.trades_max_par_jour,
                "duree_expiration_s": self.reglages.duree_expiration_s,
                "payout": self.reglages.payout,
            }
        )

        return {
            "statut": self.statut.value,
            "mode": self.mode,
            "actif": self.actif,
            "capital": round(self.capital, 2),
            "capital_initial": self.settings.capital_initial,
            "pnl_session": round(self.capital - self.settings.capital_initial, 2),
            "pct_session": round(
                100 * (self.capital - self.settings.capital_initial) / self.settings.capital_initial,
                2,
            ),
            "solde": {
                "valeur": round(
                    self.solde_plateforme
                    if self.solde_plateforme is not None
                    else self.capital,
                    2,
                ),
                "origine": self.origine_solde,
                "note": self.note_solde,
                "plateforme_disponible": self.solde_plateforme is not None,
            },
            "uptime_s": int(maintenant - self.debut_ts),
            "strategie": self.strategie.etat.instantane(),
            "risque": self.risque.etat.instantane(self.risque.limites, self.capital),
            "statistiques": stats,
            "connexion": {
                "etat": self.source.etat.value,
                "source": getattr(self.source, "nom", "indisponible"),
                "ticks": self.nb_ticks,
                "signaux": self.nb_signaux,
                "refus": self.nb_refus,
            },
            "ordre_en_cours": None
            if ordre is None
            else {
                "identifiant": ordre.identifiant,
                "direction": ordre.direction.value,
                "montant": ordre.montant,
                "prix_entree": ordre.prix_entree,
                "restant_s": max(0.0, round(ordre.echeance_ts - maintenant, 1)),
                "progression": round(
                    min(1.0, max(0.0, (maintenant - ordre.ouverture_ts) / max(ordre.expiration_s, 1e-9))),
                    3,
                ),
            },
            "dernier_prix": self.dernier_tick.prix if self.dernier_tick else None,
            "dernier_signal": self.dernier_signal,
            "alertes": [
                {"niveau": a.niveau, "message": a.message, "ts": int(a.ts)}
                for a in list(self.alertes)[-8:][::-1]
            ],
            "config": config,
            "reglages": self.reglages.instantane(),
            "trades": [t.dict_public() for t in self.journal.trades[-40:][::-1]],
        }

    # ------------------------------------------------------------------ #
    #  Cycle de vie
    # ------------------------------------------------------------------ #
    def demarrer_en_tache(self) -> None:
        if self._tache and not self._tache.done():
            return
        self._arret.clear()
        self.statut = Statut.EN_COURS
        self._tache = asyncio.create_task(self._boucle(), name="moteur-trading")

    @property
    def tache_active(self) -> bool:
        """Vrai si la boucle tourne (même en pause, la tâche reste vivante)."""
        return self._tache is not None and not self._tache.done()

    def mettre_en_pause(self) -> None:
        self._suspendre("Bot mis en pause par l'opérateur.")

    def _suspendre(self, message: str) -> None:
        """Suspension automatique ou manuelle (même effet, message distinct)."""
        self._pause.clear()
        self.statut = Statut.EN_PAUSE
        self.alerte("avertissement", message)
        self._diffuser()

    def reprendre(self) -> None:
        self._pause.set()
        self.statut = Statut.EN_COURS
        self.alerte("info", "Reprise du trading.")
        self._diffuser()

    async def arreter(self) -> None:
        self._arret.set()
        self._pause.set()
        if self._veille:
            self._veille.cancel()
            self._veille = None
        if self._tache:
            self._tache.cancel()
            try:
                await self._tache
            except (asyncio.CancelledError, Exception):
                pass
        self.execution.annuler()
        await self.source.fermer()
        self.statut = Statut.ARRETE
        self._diffuser()

    # ------------------------------------------------------------------ #
    #  Réglages à chaud et changement de mode
    # ------------------------------------------------------------------ #
    def appliquer_reglages(self, nouveaux: Reglages) -> None:
        """Applique des réglages validés, sans redémarrer la boucle."""
        self.reglages.copier_depuis(nouveaux)
        self.risque.limites = Limites(
            montant_max_position=nouveaux.taille_max_position,
            fraction_risque_par_trade=nouveaux.fraction_risque_par_trade,
            perte_max_journaliere=nouveaux.perte_max_journaliere,
            trades_max_par_jour=nouveaux.trades_max_par_jour,
        )
        self.execution.courtier.payout = nouveaux.payout
        self.alerte(
            "info",
            f"Réglages appliqués : mise {nouveaux.montant_trade:.2f} $ · "
            f"plafond {nouveaux.taille_max_position:.2f} $ · "
            f"risque {nouveaux.fraction_risque_par_trade:.2%} · "
            f"quota {nouveaux.trades_max_par_jour}/jour · "
            f"expiration {nouveaux.duree_expiration_s} s.",
        )
        self._diffuser()

    def _construire_source(self, session: Any | None) -> SourceDePrix:
        """Reconstruit le flux de prix selon le mode et le flux choisi."""
        if (
            self.mode == "OBSERVATION"
            and session is not None
            and getattr(session, "configuree", False)
        ):
            return ConnexionPocketOption(
                ssid=session.ssid or "",
                actif=self.settings.actif,
                url=session.url,
                journal=self.journal,
            )
        if self.settings.mt5_demo:
            # Ticks réels du terminal Weltrade MT5 (compte DÉMO).
            return SourceMT5(
                symbole=self.settings.mt5_symbole,
                intervalle_s=self.settings.intervalle_tick_s
                / max(self.settings.facteur_vitesse, 0.01),
                chemin=self.settings.mt5_chemin_terminal,
                journal=self.journal,
            )
        if self.reglages.flux_prix == "binance":
            return FluxBinance(
                symbole=self.settings.actif_binance,
                intervalle_s=self.settings.intervalle_tick_s
                / max(self.settings.facteur_vitesse, 0.01),
                journal=self.journal,
            )
        return FluxSynthetique(
            actif=self.settings.actif,
            intervalle_s=self.settings.intervalle_tick_s
            / max(self.settings.facteur_vitesse, 0.01),
            graine=self.settings.graine_aleatoire,
            pas_virtuel=(
                self.settings.intervalle_tick_s
                if self.settings.facteur_vitesse >= 50
                else None
            ),
        )

    @property
    def actif(self) -> str:
        """Instrument réellement suivi (BTCUSDT avec le flux Binance)."""
        valeur = getattr(self.source, "actif", "") or self.settings.actif
        return valeur

    async def rafraichir_solde(self, session: Any | None = None) -> None:
        """Met à jour le solde affiché (simulé ou compte, en lecture seule)."""
        if self.settings.mt5_demo:
            valeur, note = await SoldeMT5Demo(
                self.settings.mt5_chemin_terminal
            ).lire()
            self.solde_plateforme = valeur
            self.origine_solde = (
                "mt5_demo" if valeur is not None else "indisponible"
            )
            self.note_solde = note
            self._diffuser()
            return
        source = construire_source_solde(self.mode, self.capital, session)
        valeur, note = await source.lire()
        if self.mode == "OBSERVATION":
            self.solde_plateforme = valeur
            self.origine_solde = "plateforme" if valeur is not None else "indisponible"
        else:
            self.solde_plateforme = None
            self.origine_solde = "simulé"
        self.note_solde = note
        self._diffuser()

    async def basculer_mode(self, mode: str, session: Any | None = None) -> None:
        """Bascule DEMO <-> OBSERVATION en reconstruisant le flux de prix.

        Le mode REEL est volontairement refusé : aucun adaptateur d'exécution
        n'est livré, l'activer reviendrait à promettre une capacité inexistante.
        """
        if mode not in ("DEMO", "OBSERVATION"):
            raise ValueError(
                "Seuls les modes DEMO et OBSERVATION sont activables à chaud."
            )
        etait_pause = self.statut is Statut.EN_PAUSE
        etait_actif = self.statut in (Statut.EN_COURS, Statut.EN_PAUSE)

        await self.arreter()
        self.mode = mode
        self.reglages.mode = mode
        self.execution.mode_reel = False
        self.execution.mode_label = mode
        # Nouveau flux = nouvelles séries : on ne mélange pas les EMA du
        # simulateur avec celles d'un flux de plateforme.
        self.strategie.reinitialiser()
        self.dernier_tick = None
        self.dernier_signal = None
        self.source = self._construire_source(session)
        self.execution.actif = self.actif
        await self.rafraichir_solde(session)
        self.alerte("info", f"Mode {mode} activé.")
        if etait_actif:
            self.demarrer_en_tache()
            if etait_pause:
                self.mettre_en_pause()
        if mode == "OBSERVATION":
            self._veille = asyncio.create_task(self._veille_observation())

    async def recharger_flux(self, session: Any | None = None) -> None:
        """Change de flux de prix à chaud (simulé <-> Binance).

        Les séries de la stratégie sont réinitialisées : on ne mélange jamais
        les EMA d'un flux avec celles d'un autre.
        """
        etait_pause = self.statut is Statut.EN_PAUSE
        etait_actif = self.statut in (Statut.EN_COURS, Statut.EN_PAUSE)

        await self.arreter()
        self.strategie.reinitialiser()
        self.dernier_tick = None
        self.dernier_signal = None
        self.source = self._construire_source(session)
        self.execution.actif = self.actif
        await self.rafraichir_solde(session)
        if self.source.nom == "binance":
            self.alerte(
                "avertissement",
                f"Flux réel Binance activé : {self.actif}. Prix du marché, "
                "ordres papier uniquement — aucun ordre réel n'est transmis.",
            )
        else:
            self.alerte("info", f"Flux de prix : simulation · {self.actif}.")
        if etait_actif:
            self.demarrer_en_tache()
            if etait_pause:
                self.mettre_en_pause()
        self._diffuser()

    async def nouvelle_campagne(self) -> dict[str, Any]:
        """Archive le journal et repart de zéro (compteurs, capital, stratégie).

        Le fichier de trades n'est pas effacé : il est déplacé dans
        `journal/campagnes/`. Sans cette remise à zéro, les statistiques
        mélangent des flux et des réglages différents — elles ne mesurent plus
        rien.
        """
        await self.arreter()
        archive = self.journal.archiver_campagne()

        self.capital = self.settings.capital_initial
        self.nb_trades_ouverts = 0
        self.nb_signaux = 0
        self.nb_refus = 0
        self.dernier_tick = None
        self.dernier_signal = None
        self.execution.ordre_en_cours = None
        self.statut = Statut.ARRETE
        self.strategie.reinitialiser()
        self.risque.cloturer_journee()

        self.alerte(
            "info",
            (
                f"Nouvelle campagne : journal archivé ({archive.name}), "
                f"solde remis à {self.capital:.2f} $."
            )
            if archive
            else f"Nouvelle campagne : compteurs remis à zéro ({self.capital:.2f} $).",
        )
        self._diffuser()
        return {
            "archive": archive.name if archive else None,
            "capital": round(self.capital, 2),
            "statut": self.statut.value,
        }

    async def reinitialiser_tout(self, session: Any | None = None) -> dict[str, Any]:
        """Archive le journal, remet les compteurs ET les réglages à zéro.

        Différence avec `nouvelle_campagne` : ici, les réglages reviennent aux
        valeurs du `.env` (mise, plafonds, quotas, mode, flux). C'est le bouton
        « repartir de zéro » après une série d'essais qui a tout mélangé.
        """
        await self.arreter()
        archive = self.journal.archiver_campagne()

        defauts = Reglages.depuis_settings(self.settings)
        self.appliquer_reglages(defauts)
        await self.basculer_mode(defauts.mode, session)

        self.capital = self.settings.capital_initial
        self.nb_trades_ouverts = 0
        self.nb_signaux = 0
        self.nb_refus = 0
        self.dernier_tick = None
        self.dernier_signal = None
        self.strategie.reinitialiser()
        self.risque.cloturer_journee()
        self.statut = Statut.ARRETE

        self.alerte(
            "info",
            (
                f"Réinitialisation complète : journal archivé ({archive.name}), "
                f"réglages du .env restaurés, solde {self.capital:.2f} $."
            )
            if archive
            else (
                f"Réinitialisation complète : réglages du .env restaurés, "
                f"solde {self.capital:.2f} $."
            ),
        )
        self._diffuser()
        return {
            "archive": archive.name if archive else None,
            "capital": round(self.capital, 2),
            "statut": self.statut.value,
            "reglages": self.reglages.instantane(),
            "flux": self.source.nom,
            "actif": self.actif,
        }

    async def _veille_observation(self) -> None:
        """Alerte si le flux réel est ouvert mais ne fournit aucun prix.

        Sans ce garde-fou, la console afficherait « connecté » avec un
        graphique figé : l'opérateur croirait à une panne silencieuse.
        """
        depart = self.nb_ticks
        try:
            await asyncio.sleep(12)
        except asyncio.CancelledError:
            return
        if self.mode != "OBSERVATION" or self.nb_ticks != depart:
            return
        self.alerte(
            "avertissement",
            "Flux plateforme muet : connexion ouverte, aucun prix reçu en 12 s. "
            "Le protocole de données n'est pas adapté — aucun trade ne sera ouvert. "
            "Repassez en mode Démo ou écrivez l'adaptateur (voir SECURITY.md).",
        )
        self._diffuser()

    async def attendre_fin(self) -> None:
        if self._tache:
            try:
                await self._tache
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------ #
    #  Boucle principale
    # ------------------------------------------------------------------ #
    async def _boucle(self) -> None:
        self.alerte(
            "info",
            f"Démarrage en mode {self.mode} sur {self.actif} "
            f"(EMA{self.settings.ema_rapide}/{self.settings.ema_lente}). "
            f"Chauffe : {self.strategie.etat.points_requis} points avant le premier signal.",
        )
        self._diffuser()
        try:
            async for tick in self.source.flux():
                if self._arret.is_set():
                    break
                await self._pause.wait()

                self.dernier_tick = tick
                self.nb_ticks += 1

                # 1) Résolution d'un ordre arrivé à échéance -----------------
                if self.execution.ordre_en_cours is not None:
                    self._tenter_resolution(tick)

                # 2) Mise à jour de la stratégie à CHAQUE tick -----------------
                #    Le marché doit être suivi même pendant un trade ouvert,
                #    sinon les EMA deviennent aveugles pendant l'expiration.
                self._traiter_signal(tick)

                # 3) Diffusion -------------------------------------------------
                if self.nb_ticks % 2 == 0 or self.execution.ordre_en_cours is None:
                    self._diffuser()

                # 3 bis) Disjoncteurs d'exploitation : on suspend proprement
                #        au lieu de marteler des refus à chaque tick.
                if self.max_trades is None and self.execution.ordre_en_cours is None:
                    if self.risque.etat.trades_du_jour >= self.risque.limites.trades_max_par_jour:
                        self._suspendre(
                            f"Quota journalier atteint "
                            f"({self.risque.etat.trades_du_jour}/"
                            f"{self.risque.limites.trades_max_par_jour}) : "
                            "trading suspendu jusqu'au prochain jour UTC."
                        )
                    elif -self.risque.etat.pnl_du_jour >= self.risque.limites.perte_max_journaliere:
                        self._suspendre(
                            f"Perte journalière maximale atteinte "
                            f"({-self.risque.etat.pnl_du_jour:.2f} $) : "
                            "trading suspendu jusqu'au prochain jour UTC."
                        )

                # 4) Campagne de test : rotation de journée simulée -----------
                if (
                    self.max_trades
                    and self.nb_trades_ouverts < self.max_trades
                    and self.risque.etat.trades_du_jour >= self.risque.limites.trades_max_par_jour
                ):
                    self.risque.cloturer_journee()
                    self.alerte(
                        "info",
                        f"Quota journalier atteint : journée suivante simulée "
                        f"({self.nb_trades_ouverts}/{self.max_trades} trades au total).",
                    )
                    self._diffuser()

                if self.max_ticks and self.nb_ticks >= self.max_ticks:
                    self.alerte("avertissement", f"Garde-fou : {self.max_ticks} ticks atteints.")
                    self.statut = Statut.TERMINE
                    self._diffuser()
                    break
                if (
                    self.max_trades
                    and self.nb_trades_ouverts >= self.max_trades
                    and self.execution.ordre_en_cours is None
                ):
                    self.alerte(
                        "info",
                        f"Campagne terminée : {self.max_trades} trades atteints.",
                    )
                    self.statut = Statut.TERMINE
                    self._diffuser()
                    break

                # 5) Courtoisie envers la boucle d'événements : en horloge
                #    virtuelle, aucun sleep n'a lieu, on cède donc la main
                #    régulièrement pour ne pas affamer le serveur web.
                if getattr(self.source, "pas_virtuel", None) and self.nb_ticks % 200 == 0:
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # erreur imprévue : on trace et on s'arrête
            self.statut = Statut.ERREUR
            self.alerte("erreur", f"Boucle interrompue : {type(exc).__name__} — {exc}")
            self._diffuser()
        finally:
            if self.statut not in (Statut.TERMINE, Statut.ERREUR):
                self.statut = Statut.ARRETE
            self._diffuser()

    # ------------------------------------------------------------------ #
    def _tenter_resolution(self, tick: Tick) -> None:
        ordre = self.execution.ordre_en_cours
        if ordre is None or tick.horodatage < ordre.echeance_ts:
            return
        try:
            _, resultat, nouveau_capital = self.execution.resoudre(tick.prix, self.capital)
            self.capital = nouveau_capital
            self.alerte(
                "info",
                f"{resultat.resultat} · {resultat.pnl:+.2f} $ · solde {self.capital:.2f} $",
            )
        except ExecutionRefusee as exc:
            self.alerte("erreur", f"Résolution impossible : {exc}")

    def _traiter_signal(self, tick: Tick) -> None:
        signal = self.strategie.maj(tick.prix, tick.horodatage)
        if signal is None or not signal.exploitable:
            return

        self.nb_signaux += 1
        self.dernier_signal = {
            "direction": signal.direction.value,
            "prix": signal.prix,
            "raison": signal.raison,
            "ts": int(signal.horodatage),
        }

        # Une position est déjà ouverte : on enregistre le signal pour la
        # traçabilité, mais on n'empile pas une seconde exposition.
        if self.execution.ordre_en_cours is not None:
            self.alerte(
                "info",
                f"Signal {signal.direction.value} ignoré : une position est déjà ouverte.",
            )
            return

        try:
            ordre: Ordre = self.execution.executer(signal, self.capital)
            self.nb_trades_ouverts += 1
            self.alerte(
                "info",
                f"Signal {ordre.direction.value} exécuté à {ordre.prix_entree:.5f} "
                f"({ordre.montant:.2f} $).",
            )
        except RisqueRefuse as exc:
            self.nb_refus += 1
            self.alerte("avertissement", f"Risque : {exc}")
        except ExecutionRefusee as exc:
            self.nb_refus += 1
            self.alerte("erreur", f"Exécution bloquée : {exc}")
