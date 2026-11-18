"""Statistiques honnêtes pour l'échantillon de trades.

En logarithmes pour que `math.lgamma` reste stable même sur des n ≈ 7 000
(et au-delà) : `math.comb` directement déborde. Placé dans le package parce
que le journal live et le backtest en ont besoin des deux.
"""

from __future__ import annotations

import math


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalle de confiance à 95 % (Wilson) — fiable sur petits échantillons."""
    k, n = int(k), int(n)
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - m), min(1.0, c + m))


def _log_pmf(k: int, n: int, p: float) -> float:
    """log(P(X = k)) pour X ~ Binomiale(n, p). Stable pour tout n."""
    k, n = int(k), int(n)
    if p <= 0.0:
        return 0.0 if k == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if k == n else -math.inf
    return (
        math.lgamma(n + 1)
        - math.lgamma(k + 1)
        - math.lgamma(n - k + 1)
        + k * math.log(p)
        + (n - k) * math.log1p(-p)
    )


def binom_pmf(k: int, n: int, p: float) -> float:
    """P(X = k) — renvoie 0.0 si la densité déborde la précision float."""
    lp = _log_pmf(k, n, p)
    return math.exp(lp) if lp > -745 else 0.0


def p_au_moins(k: int, n: int, p0: float) -> float:
    """P(X >= k) si la vraie probabilité valait p0."""
    k, n = int(k), int(n)
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return min(1.0, math.fsum(binom_pmf(i, n, p0) for i in range(k, n + 1)))


def p_value_exacte(k: int, n: int, p0: float) -> float:
    """Test binomial exact bilatéral (méthode des petites probabilités)."""
    k, n = int(k), int(n)
    if n <= 0:
        return 1.0
    log_obs = _log_pmf(k, n, p0)
    return min(
        1.0,
        math.fsum(
            binom_pmf(i, n, p0)
            for i in range(n + 1)
            if _log_pmf(i, n, p0) <= log_obs + 1e-12
        ),
    )


def seuil_rentabilite(payout: float) -> float:
    """Réussite minimale pour être à l'équilibre avec ce payout (binaire)."""
    return 1.0 / (1.0 + payout)
