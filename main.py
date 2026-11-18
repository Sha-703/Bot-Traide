"""Point d'entrée unique du projet Pocket Lab.

Usages :
    python main.py                 # console web sur http://127.0.0.1:8080
    python main.py --headless      # boucle de trading dans le terminal
    python main.py --simulation 100  # campagne de 100 trades + rapport

Le mode est entièrement piloté par `.env` (MODE_REEL). Aucun argument CLI ne
permet d'activer le réel : c'est une décision de configuration explicite.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace

try:  # Dépendances installées dans .venv (voir requirements.txt)
    from bot.config import ConfigError, Settings, charger_config
    from bot.engine import Moteur
    from bot.journalisation import Journal
except ModuleNotFoundError as exc:  # message actionnable plutôt qu'une stacktrace
    print(
        "\n  ✖ Dépendance manquante : "
        f"{exc.name}\n\n"
        "  Vous lancez probablement le Python système au lieu de l'environnement\n"
        "  virtuel du projet. Utilisez le lanceur fourni :\n\n"
        "      .\\demarrer.cmd\n\n"
        "  ou, manuellement :\n\n"
        "      .\\.venv\\Scripts\\Activate.ps1\n"
        "      pip install -r requirements.txt\n"
        "      python main.py\n",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


def forcer_utf8() -> None:
    """Évite les UnicodeEncodeError sur les consoles Windows en cp1252."""
    for flux in (sys.stdout, sys.stderr):
        try:
            if (flux.encoding or "").lower().replace("-", "") != "utf8":
                flux.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):  # redirection, console exotique
            pass


def afficher_rapport(settings: Settings, journal: Journal) -> None:
    """Restitue les indicateurs clés après une campagne de simulation."""
    stats = journal.statistiques()
    seuil = 100 / (1 + settings.payout)
    print()
    print("  " + "─" * 62)
    print("   RAPPORT DE SIMULATION — mode démo, aucun argent réel engagé")
    print("  " + "─" * 62)
    lignes = [
        ("Actif", settings.actif),
        ("Trades résolus", stats["total"]),
        ("Gagnants / Perdants", f"{stats['gagnants']} / {stats['perdants']}"),
        ("Taux de réussite", f"{stats['taux_reussite']} %"),
        ("Seuil de rentabilité", f"{seuil:.1f} % (payout {settings.payout:.0%})"),
        ("P&L total", f"{stats['pnl_total']:+.2f} $"),
        ("Espérance par trade", f"{stats['esperance']:+.2f} $"),
        ("Gain moyen / perte moyenne", f"{stats['gain_moyen']:.2f} $ / {stats['perte_moyenne']:.2f} $"),
        ("Ratio gain/perte", stats["ratio_gain_perte"]),
        ("Profit factor", stats["profit_factor"]),
        ("Drawdown maximal", f"{stats['drawdown_max']:.2f} $"),
        ("Journal", str(journal.chemin_trades)),
    ]
    for label, valeur in lignes:
        print(f"   {label:<28} {valeur}")
    print("  " + "─" * 62)
    if stats["total"] and stats["taux_reussite"] < seuil:
        print("   ⚠ Taux de réussite INFÉRIEUR au seuil de rentabilité :")
        print("     la stratégie est perdante sur cet échantillon.")
    elif stats["total"]:
        print("   ⚠ Échantillon court : un avantage statistique n'est PAS démontré.")
        print("     Ne considérez pas ce résultat comme une preuve de rentabilité.")
    print()


def mode_headless(settings: Settings, nb_trades: int | None) -> int:
    """Exécute le moteur sans interface web (terminal / CI / campagne)."""
    moteur = Moteur(settings)
    moteur.max_trades = nb_trades

    async def _courir() -> None:
        moteur.demarrer_en_tache()
        await moteur.attendre_fin()

    try:
        asyncio.run(_courir())
    except KeyboardInterrupt:
        print("\n  Arrêt demandé par l'utilisateur.")
    afficher_rapport(settings, moteur.journal)
    return 0


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(
        prog="pocket-lab",
        description="Bot de trading supervisé — simulation d'abord, prudence ensuite.",
    )
    parseur.add_argument("--headless", action="store_true", help="sans interface web")
    parseur.add_argument(
        "--simulation",
        type=int,
        metavar="N",
        help="campagne de N trades en temps accéléré puis rapport",
    )
    args = parseur.parse_args(argv)

    forcer_utf8()

    try:
        settings = charger_config()
    except ConfigError as exc:
        print(f"\n  ✖ Configuration invalide :\n    {exc}\n", file=sys.stderr)
        return 2

    if settings.mode_reel:
        print(
            "  ✖ MODE_REEL=True : l'exécution réelle est verrouillée par conception.\n"
            "    Aucun adaptateur courtier réel n'est livré. Voir SECURITY.md.",
            file=sys.stderr,
        )
        return 3

    if args.simulation:
        settings = replace(
            settings,
            facteur_vitesse=max(settings.facteur_vitesse, 1000.0),
            graine_aleatoire=settings.graine_aleatoire or 42,
        )
        print(f"\n  Campagne de {args.simulation} trades (temps virtuel, mode démo)…")
        return mode_headless(settings, args.simulation)

    if args.headless:
        return mode_headless(settings, None)

    from bot.serveur import lancer

    lancer(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
