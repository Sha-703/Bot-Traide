"""Sweep de règles candidates, mesurées dans les mêmes conditions.

Chaque règle passe par le même rejeu (une position à la fois, cooldown,
résolution close→close). La fenêtre de test n'a jamais servi à choisir.

Usage :
    python scripts/backtest_candidats.py --actif BTCUSDT --jours 90
    python scripts/backtest_candidats.py --actif EURUSDT --jours 30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from bot.candidats import BollingerFade, BreakoutDonchian, RetourRsi  # noqa: E402
from bot.klines import iterer_ticks, jours_selectionnes  # noqa: E402
from bot.replay import EtatEcheance  # noqa: E402
from bot.statistiques import p_au_moins, seuil_rentabilite, wilson  # noqa: E402
from bot.strategie import StrategieMoyennesMobiles  # noqa: E402


def candidats(cooldown: int) -> dict:
    return {
        "EMA 9/21 (base)": lambda: StrategieMoyennesMobiles(9, 21, 1.0, cooldown),
        "RSI 14 survente/achat": lambda: RetourRsi(14, 30.0, 70.0, cooldown),
        "RSI 7  25/75": lambda: RetourRsi(7, 25.0, 75.0, cooldown),
        "Bollinger 20 k=2": lambda: BollingerFade(20, 2.0, cooldown),
        "Bollinger 20 k=3": lambda: BollingerFade(20, 3.0, cooldown),
        "Donchian 20": lambda: BreakoutDonchian(20, cooldown),
    }


def jouer_fenetre(prefixe, jours, noms: dict, expiries: list[int],
                  payout: float, mise: float, cooldown: int, cache: Path) -> dict:
    etats = {
        (nom, e): EtatEcheance(e, strategie=fabrique(), cooldown_s=cooldown,
                               payout=payout, mise=mise)
        for nom, fabrique in noms.items()
        for e in expiries
    }
    for ts, close in iterer_ticks(prefixe, jours, cache):
        for etat in etats.values():
            etat.tick(ts, close)
    return {(nom, e): etat.stats for (nom, e), etat in etats.items()}


def afficher(titre: str, stats: dict, payout: float) -> None:
    seuil = 100 * seuil_rentabilite(payout)
    print("=" * 118)
    print(titre)
    print(f"Payout {100 * payout:.0f} % · seuil de rentabilité {seuil:.1f} % · cooldown par défaut")
    print("-" * 118)
    print(f"{'règle':<22} {'écheance':>9} {'trades':>7} {'réussite':>9} "
          f"{'IC 95 %':>19} {'P/L':>10} {'espér.':>8}")
    print("-" * 118)
    for (nom, e), s in stats.items():
        gens = s["gagnants"] + s["perdants"]
        if gens == 0:
            print(f"{nom:<22} {e:>9}   0 trade")
            continue
        taux = 100 * s["gagnants"] / gens
        bas, haut = wilson(int(s["gagnants"]), int(gens))
        esperance = s["pnl"] / gens
        print(f"{nom:<22} {e:>8} s {int(s['trades']):>7} {taux:>8.1f} % "
              f"[{100 * bas:5.1f} %, {100 * haut:5.1f} %] {s['pnl']:>+9.2f} $ "
              f"{esperance:>+7.3f} $")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actif", default="BTCUSDT")
    parser.add_argument("--jours", type=int, default=30)
    parser.add_argument("--jours-test", type=int, default=10)
    parser.add_argument("--expiries", default="5,15,60")
    parser.add_argument("--payout", type=float, default=0.87)
    parser.add_argument("--mise", type=float, default=1.0)
    parser.add_argument("--cooldown", type=int, default=30)
    parser.add_argument("--cache", type=Path, default=RACINE / "backtest" / "data")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    jours = jours_selectionnes(args.jours)
    jours_train = jours[:-args.jours_test]
    jours_test = jours[-args.jours_test:]
    noms = candidats(args.cooldown)
    expiries = sorted({int(x) for x in args.expiries.split(",") if x.strip()})

    print(f"Sweep {args.actif} · {len(jours)} jours · expiries {expiries}\n")

    print("Calcul calibration…", flush=True)
    stats_train = jouer_fenetre(args.actif, jours_train, noms, expiries,
                                args.payout, args.mise, args.cooldown, args.cache)
    print("Calcul test…", flush=True)
    stats_test = jouer_fenetre(args.actif, jours_test, noms, expiries,
                               args.payout, args.mise, args.cooldown, args.cache)

    afficher(f"CALIBRATION — {jours_train[0].isoformat()} → {jours_train[-1].isoformat()}",
             stats_train, args.payout)
    print()
    afficher(f"TEST (hors calibration) — {jours_test[0].isoformat()} → {jours_test[-1].isoformat()}",
             stats_test, args.payout)

    seuil = 100 * seuil_rentabilite(args.payout)
    print("=" * 118)
    print("VERDICT SUR TEST (seule fenêtre non biaisée)")
    for (nom, e), s in stats_test.items():
        gens = s["gagnants"] + s["perdants"]
        if gens == 0:
            print(f"  {nom:<22} {e:>3} s · aucun trade")
            continue
        taux = 100 * s["gagnants"] / gens
        p = p_au_moins(int(s["gagnants"]), int(gens), seuil / 100)
        ok = taux > seuil and p < 0.05
        print(f"  {nom:<22} {e:>3} s · {int(gens):>5} trades · {taux:5.1f} % "
              f"· P = {100 * p:5.2f} % · {'AVANTAGE' if ok else 'pas d’avantage'}")
    print("=" * 118)


if __name__ == "__main__":
    main()
