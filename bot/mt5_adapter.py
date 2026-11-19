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


def fermer_bougies(symbole: str, n: int = 25, chemin: str | None = None) -> list[float]:
    """Lit les `n` dernières bougies M1 fermées depuis MT5 (plus récente en dernier)."""
    if n <= 0:
        return []
    mt5 = _connecter(chemin)  # exiger_demo = True
    mt5.symbol_select(symbole, True)
    rates = mt5.copy_rates_from_pos(symbole, mt5.TIMEFRAME_M1, 0, n + 1)
    if rates is None or len(rates) == 0:
        return []
    # La plus récente (position 0) est en cours de formation — on l'ignore ;
    # les bougies clôturées sont positions 1..len-1.
    closes = [float(r[4]) for r in rates[1:]]
    return closes[-max(1, int(n)):]


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


CREDITS_EN_MEMOIRE: dict | None = None  # jamais sérialisé, jamais logué


def _initialize_mt5(chemin: str | None = None, login=None, password=None, server=None):
    """Initialise MT5 et exige un compte DÉMO (trade_mode ∈ {0, 1})."""
    mt5 = _charger_mt5()
    try:
        mt5.shutdown()
    except Exception:
        pass
    chemin = chemin_mt5(chemin)
    if login is not None and password is not None and server:
        ok = mt5.initialize(
            path=chemin,
            login=int(login),
            server=str(server),
            password=str(password),
        )
    else:
        ok = bool(mt5.initialize()) or bool(mt5.initialize(path=chemin))
    if not ok:
        code, detail = mt5.last_error()
        raise MT5Indisponible(
            f"Terminal MT5 indisponible (code {code} : {detail})."
        )
    info = mt5.account_info()
    if info is None:
        raise MT5Indisponible(
            "Terminal démarré mais aucun compte connecté — impossible de vérifier DEMO."
        )
    if getattr(info, "trade_mode", None) not in (0, 1):
        raise MT5Indisponible(
            f"Compte non démo détecté (trade_mode={getattr(info, 'trade_mode', '?')}). "
            "La console refuse tout compte autre que DÉMO, par design."
        )
    return mt5


def se_connecter_avec_credits(login, password, server, chemin: str | None = None) -> dict[str, Any]:
    """Connexion à un compte DÉMO via identifiants — mémoire seulement."""
    try:
        login_entier = int(str(login).strip())
    except (TypeError, ValueError) as exc:
        raise MT5Indisponible(
            f"Identifiant invalide (doit être un nombre) : {login!r}"
        ) from exc
    mot_de_passe = str(password or "").strip()
    if not mot_de_passe:
        raise MT5Indisponible("Mot de passe vide.")
    server = str(server or "").strip()
    if not server:
        raise MT5Indisponible("Serveur requis (ex. Weltrade-Demo).")

    global CREDITS_EN_MEMOIRE
    ancien = CREDITS_EN_MEMOIRE
    try:
        CREDITS_EN_MEMOIRE = {
            "login": login_entier,
            "password": mot_de_passe,
            "server": server,
        }
        _initialize_mt5(chemin, login=login_entier, password=mot_de_passe, server=server)
    except MT5Indisponible:
        CREDITS_EN_MEMOIRE = ancien
        raise
    return informations_compte(chemin)


def oublier_connexion_mt5() -> None:
    """Oublie le mot de passe en mémoire ; reprend la détection du terminal."""
    global CREDITS_EN_MEMOIRE
    CREDITS_EN_MEMOIRE = None
    try:
        espace = _charger_mt5()
        espace.shutdown()
    except Exception:
        pass


def _connecter(chemin: str | None = None, exiger_demo: bool = True):
    """Connexion : identifiants stockés en mémoire, sinon terminal déjà démarré."""
    if CREDITS_EN_MEMOIRE:
        return _initialize_mt5(
            chemin,
            login=CREDITS_EN_MEMOIRE["login"],
            password=CREDITS_EN_MEMOIRE["password"],
            server=CREDITS_EN_MEMOIRE["server"],
        )
    return _initialize_mt5(chemin)


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


def diagnostiquer_mt5(chemin: str | None, symbole: str, actif: bool) -> dict[str, Any]:
    """Rapport complet de l'état MT5 pour la console.

    N'expose aucun secret, seulement les informations du compte démo.
    """
    if not actif:
        return {
            "configure": False,
            "ouvert": False,
            "source": "indisponible",
            "symbole": symbole,
            "erreur": "MT5_DEMO=false dans .env — la console ne parle pas au terminal.",
            "note": "Activez MT5_DEMO=true, redémarrez, et gardez le terminal ouvert.",
        }
    try:
        info = informations_compte(chemin)
        return {
            "configure": True,
            "ouvert": True,
            "source": "mt5_demo",
            "symbole": symbole,
            **info,
            "erreur": None,
            "note": (
                f"Flux mt5_demo · {symbole} · compte DÉMO #{info['login']} "
                f"({info['serveur']})"
            ),
        }
    except MT5Indisponible as exc:
        return {
            "configure": True,
            "ouvert": False,
            "source": "indisponible",
            "symbole": symbole,
            "erreur": str(exc),
            "note": "Ouvrez le terminal Weltrade MT5 et connectez-vous au compte DÉMO.",
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


# --------------------------------------------------------------------------- #
#  Ordres marché (DÉMO uniquement — exigence de compte démo vérifiée)
# --------------------------------------------------------------------------- #

MAGIQUE = 777001  # signature exclusive de ce bot — jamais un ordre humain


def _valider_volume(mt5, info_symbole, volume: float) -> float:
    """Ajuste au volume minimum et au pas autorisé du symbole."""
    step = info_symbole.volume_step
    minimum = info_symbole.volume_min
    ajust = minimum + round((volume - minimum) / step) * step
    return max(minimum, round(ajust, 2))


def _mode_remplissage(mt5, info_symbole):
    """Choisit le mode de remplissage permis par le symbole (FOK sinon IOC, sinon RETURN)."""
    mode = getattr(info_symbole, "filling_mode", 0)
    if mode & 1:
        return mt5.ORDER_FILLING_FOK
    if mode & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def envoyer_ordre_marche(
    symbole: str,
    direction,  # Direction.ACHAT | Direction.VENTE
    volume: float,
    sl_points: int,
    tp_points: int,
    chemin: str | None = None,
    magique: int = MAGIQUE,
    commentaire: str = "PocketLab",
) -> dict[str, Any]:
    """Envoie un ordre MARCHÉ sur le compte DÉMO. Refuse tout compte non-démo.

    Retourne ticket, deal, sl, tp, volume, prix.
    """
    from .strategie import Direction as _Dir

    mt5 = _connecter(chemin, exiger_demo=True)
    mt5.symbol_select(symbole, True)

    info = mt5.symbol_info(symbole)
    tick = mt5.symbol_info_tick(symbole)
    if info is None or tick is None:
        raise MT5Indisponible(f"Symbole {symbole} indisponible dans le terminal.")
    if tick.bid <= 0 or tick.ask <= 0:
        raise MT5Indisponible(f"Cotation muette pour {symbole} (marché fermé ?).")

    point = info.point
    volume = _valider_volume(mt5, info, volume)

    if direction is _Dir.ACHAT:
        type_ordre = mt5.ORDER_TYPE_BUY
        prix = tick.ask
        sl = prix - sl_points * point
        tp = prix + tp_points * point
    elif direction is _Dir.VENTE:
        type_ordre = mt5.ORDER_TYPE_SELL
        prix = tick.bid
        sl = prix + sl_points * point
        tp = prix - tp_points * point
    else:
        raise MT5Indisponible(f"Direction inattendue : {direction!r}")

    requete = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbole,
        "volume": volume,
        "type": type_ordre,
        "price": round(prix, info.digits),
        "sl": round(sl, info.digits),
        "tp": round(tp, info.digits),
        "deviation": 20,
        "magic": magique,
        "comment": str(commentaire)[:31],
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _mode_remplissage(mt5, info),
    }

    res = mt5.order_send(requete)
    if res is None:
        raise MT5Indisponible("L'ordre a échoué (aucun retour du terminal).")
    retcode = getattr(res, "retcode", None)
    if retcode != mt5.TRADE_RETCODE_DONE:
        commentaire_err = getattr(res, "comment", "")
        raise MT5Indisponible(
            f"Ordre refusé (retcode={retcode}) {commentaire_err}".strip()
        )
    return {
        "ticket": getattr(res, "order", None),
        "deal": getattr(res, "deal", None),
        "volume": volume,
        "prix": prix,
        "sl": sl,
        "tp": tp,
    }


def position_du_symbole(symbole: str, chemin: str | None = None):
    """Position ouverte du symbole (None si aucune). Refuse compte non-démo."""
    mt5 = _connecter(chemin, exiger_demo=True)
    positions = mt5.positions_get(symbol=symbole)
    return positions[0] if positions else None


def fermer_position(symbole: str, chemin: str | None = None) -> bool:
    """Ferme la position du symbole au marché si elle existe."""
    from .strategie import Direction as _Dir  # noqa: F401 (symétrie)

    mt5 = _connecter(chemin, exiger_demo=True)
    position = mt5.positions_get(symbol=symbole)
    if not position:
        return False
    p = position[0]
    info = mt5.symbol_info(symbole)
    tick = mt5.symbol_info_tick(symbole)
    if info is None or tick is None:
        raise MT5Indisponible(f"{symbole} indisponible pour fermeture.")
    if p.type == mt5.POSITION_TYPE_BUY:
        type_ordre = mt5.ORDER_TYPE_SELL
        prix = tick.bid
    else:
        type_ordre = mt5.ORDER_TYPE_BUY
        prix = tick.ask
    requete = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbole,
        "volume": p.volume,
        "type": type_ordre,
        "price": round(prix, info.digits),
        "position": p.ticket,
        "deviation": 20,
        "magic": MAGIQUE,
        "comment": "fermeture PocketLab",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _mode_remplissage(mt5, info),
    }
    res = mt5.order_send(requete)
    return bool(res and getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE)


def pnl_approx_eurusd(entree: float, sortie: float, volume: float, direction) -> float:
    """Approx de P&L pour EUR/USD (contract 100.000).

    N'est pas exact à la virgule (commissions, swap ignorés) — le solde
    réel est affiché via account_info(). Ce calcul sert uniquement au
    journal / aux gardes-fous.
    """
    from .strategie import Direction as _Dir

    contrat = 100000.0
    diff = (sortie - entree) if direction is _Dir.ACHAT else (entree - sortie)
    return round(diff * contrat * volume, 2)


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
