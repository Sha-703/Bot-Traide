"""Backtest historique sur les klines 1 seconde de Binance.

Comparaison de plusieurs échéances sur la même série de prix, avec séparation
calibration / test (walk-forward) : on n'annonce jamais le résultat du mois de
test comme s'il avait servi à calibrer.

Usage :
    python scripts/backtest_historique.py --jours 30
    python scripts/backtest_historique.py --jours 7 --expiries 15,60,300

Téléchargement : https://data.binance.vision/ (archive publique, ~2,4 Mo/jour,
86 400 ticks). Cache local dans `backtest/data/` (gitignore).
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from bot.klines import iterer_ticks, jours_selectionnes  # noqa: E402
from bot.replay import rejouer  # noqa: E402
from bot.statistiques import p_au_moins, seuil_rentabilite, wilson  # noqa: E402


def jouer(prefixe: str, jours: list, expiries: list[int],
          args: argparse.Namespace, cache: Path) -> dict[int, dict]:
    parametres = dict(
        ema_rapide=args.ema_rapide,
        ema_lente=args.ema_lente,
        seuil_ecart_bps=args.seuil_ecart,
        cooldown_s=args.cooldown,
        payout=args.payout,
        mise=args.mise,
    )
    # rejouer() accepte un itérateur : chaque tick n'est lu qu'une fois.
    return rejouer(iterer_ticks(prefixe, jours, cache), expiries, **parametres)


def afficher(titre: str, stats: dict[int, dict], payout: float) -> None:
    seuil = 100 * seuil_rentabilite(payout)
    print("=" * 108)
    print(titre)
    print(f"Payout {100 * payout:.0f} % · seuil de rentabilité {seuil:.1f} %")
    print("-" * 108)
    print(
        f"{'échéance':>9} {'ticks':>9} {'trades':>7} {'G':>5} {'P':>5} "
        f"{'réussite':>8} {'IC 95 %':>19} {'P/L':>10} {'espér.':>8} "
        f"{'DD max':>9} {'série':>6}"
    )
    print("-" * 108)
    for e, s in sorted(stats.items()):
        gens = s["gagnants"] + s["perdants"]
        taux = 100 * s["gagnants"] / gens if gens else 0.0
        bas, haut = wilson(s["gagnants"], gens)
        esperance = s["pnl"] / gens if gens else 0.0
        dd = -s["dd_max"]
        print(
            f"{str(e) + ' s':>9} {int(s['ticks']):>9} {int(s['trades']):>7} "
            f"{int(s['gagnants']):>5} {int(s['perdants']):>5} {taux:>7.1f} % "
            f"[{100 * bas:5.1f} %, {100 * haut:5.1f} %] "
            f"{s['pnl']:>+9.2f} $ {esperance:>+7.3f} $ {dd:>8.2f} $ {int(s['serie_max']):>6}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actif", default="BTCUSDT")
    parser.add_argument("--jours", type=int, default=30)
    parser.add_argument("--jours-test", type=int, default=10,
                        help="derniers jours réservés au test hors calibration")
    parser.add_argument("--expiries", default="1,5,15,30,60,120,300")
    parser.add_argument("--payout", type=float, default=0.87)
    parser.add_argument("--mise", type=float, default=1.0)
    parser.add_argument("--ema-rapide", type=int, default=9)
    parser.add_argument("--ema-lente", type=int, default=21)
    parser.add_argument("--seuil-ecart", type=float, default=1.0)
    parser.add_argument("--cooldown", type=int, default=30)
    parser.add_argument("--cache", type=Path, default=RACINE / "backtest" / "data")
    args = parser.parse_args()

    if not 0 < args.payout <= 1:
        parser.error("--payout doit être compris entre 0 exclu et 1.")

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args.cache.mkdir(parents=True, exist_ok=True)

    jours = jours_selectionnes(args.jours)
    if args.jours_test >= len(jours):
        parser.error("--jours-test doit être inférieur à --jours.")
    jours_train = jours[:-args.jours_test]
    jours_test = jours[-args.jours_test:]
    expiries = sorted({int(x) for x in args.expiries.split(",") if x.strip()})

    print(
        f"Backtest {args.actif} · {len(jours)} jours "
        f"({jours[0].isoformat()} → {jours[-1].isoformat()})"
    )
    print(f"  calibration : {len(jours_train)} jours · test : {len(jours_test)} jours")
    print(f"  expiries    : {expiries}")
    print(f"  cache       : {args.cache}\n")

    train = jouer(args.actif, jours_train, expiries, args, args.cache)
    test = jouer(args.actif, jours_test, expiries, args, args.cache)

    afficher(
        f"CALIBRATION — {jours_train[0].isoformat()} → {jours_train[-1].isoformat()}",
        train, args.payout,
    )
    print()
    afficher(
        f"TEST (hors calibration) — {jours_test[0].isoformat()} → {jours_test[-1].isoformat()}",
        test, args.payout,
    )

    # Verdict : la fenêtre de test est la seule non biaisée à la calibration.
    seuil = 100 * seuil_rentabilite(args.payout)
    print("=" * 108)
    print("VERDICT SUR LA FENÊTRE DE TEST (seule donnée non biaisée)")
    for e, s in sorted(test.items()):
        gens = s["gagnants"] + s["perdants"]
        if gens == 0:
            print(f"  {e:>4} s · aucun trade")
            continue
        taux = 100 * s["gagnants"] / gens
        p = p_au_moins(int(s["gagnants"]), int(gens), seuil / 100)
        avantage = taux > seuil and p < 0.05
        print(
            f"  {e:>4} s · {int(gens):>6} trades · {taux:5.1f} % "
            f"· P(≥{int(s['gagnants'])}/{int(gens)} sans avantage) = {100 * p:5.1f} % "
            f"· {'AVANTAGE POSSIBLE' if avantage else 'pas d’avantage détecté'}"
        )
    print("=" * 108)
    print("Aucune conclusion ne prouve la rentabilité future. "
          "BTC/USDT n'est pas l'EUR/USD de Pocket Option.")


if __name__ == "__main__":
    main()
