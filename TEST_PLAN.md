# Plan de test — Pocket Lab

Objectif : valider la **plomberie** (données → signal → risque → exécution → journal),
mesurer honnêtement ce qui se passe sur 100 trades simulés, et fixer des critères
**go / no-go** avant d'envisager quoi que ce soit de plus sérieux.

> ⚠️ 100 trades ne démontrent aucun avantage statistique. Un taux de réussite de 60 %
> sur 100 trades peut parfaitement venir du hasard. Ce plan sert à détecter des
> **bugs** et des **comportements dangereux**, pas à valider une rentabilité.

---

## 1. Niveaux de test

| Niveau | Commande | Ce qui est vérifié |
|---|---|---|
| Unitaire | `pytest -q` | Config, risque, stratégie, verrous d'exécution, journal |
| Intégration | `python main.py --headless` | Boucle complète en temps réel, reprise, arrêt propre |
| Campagne | `python scripts/simuler_100_trades.py` | 100 trades × 5 graines, reproductibles |
| Interface | `python main.py` puis `http://127.0.0.1:8080` | SSE, courbes, pause/reprise, export CSV |
| Robustesse | tests négatifs ci-dessous | Refus du réel, refus de risque, coupures réseau |

---

## 2. Campagne de 100 trades simulés

### Protocole
1. `.env` en démo (par défaut) : `MODE_REEL=False`, `GRAINE_ALEATOIRE=` vide.
2. Lancer :
   ```powershell
   python main.py --simulation 100
   # ou, pour comparer plusieurs tirages :
   python scripts/simuler_100_trades.py --trades 100 --graines 5
   ```
3. Consigner pour chaque graine : taux de réussite, P&L, drawdown, profit factor, espérance.
4. Rejouer avec une graine fixe (`GRAINE_ALEATOIRE=42`) et vérifier **l'identité
   parfaite** des résultats : le déterminisme est un test à part entière.
5. Ouvrir `journal/campagnes/graine_*.csv` et vérifier ligne à ligne :
   - `montant` ≤ `TAILLE_MAX_POSITION` ;
   - `pnl` ∈ {`+montant × payout`, `−montant`, `0`} ;
   - `solde_apres` = `solde_apres` précédent + `pnl` ;
   - `trades_du_jour` ≤ `TRADES_MAX_PAR_JOUR` (console).

### Indicateurs à surveiller

| Indicateur | Formule | Seuil d'alerte |
|---|---|---|
| **Taux de réussite** | gagnants / trades résolus | < **53,5 %** avec payout 87 % ⇒ perdant par construction |
| **Seuil de rentabilité** | `100 / (1 + payout)` | doit être affiché et comparé, pas deviné |
| **Espérance / trade** | P&L total / trades | négative ⇒ aucune raison de continuer |
| **Drawdown max** | plus forte baisse depuis un sommet | > 3 × `PERTE_MAX_JOURNALIERE` ⇒ sizing trop agressif |
| **Ratio risque/récompense** | gain moyen / perte moyenne | avec payout 0,87 il plafonne à **0,87** : c'est structurel |
| **Profit factor** | gains bruts / pertes brutes | < 1,2 ⇒ fragile, non exploitable |
| **Série perdante max** | plus longue suite de pertes | > 8 ⇒ vérifier le respect du plafond de perte |
| **Refus de risque** | compteur `refus` (console) | > 20 % des signaux ⇒ limites mal calibrées |

### Interprétation attendue
Avec un générateur **aléatoire** et un payout de 87 %, la probabilité de gain par trade
est ≈ 50 % ⇒ espérance négative ≈ −6,5 % du montant misé. Un résultat positif sur
une graine est du **bruit**. C'est exactement le piège que ce plan cherche à rendre visible.

---

## 3. Tests négatifs (doivent tous « échouer proprement »)

| Scénario | Action | Résultat attendu |
|---|---|---|
| `.env` sans `MODE_REEL` | `python main.py` | Refus immédiat, message explicite, code de sortie ≠ 0 |
| `MODE_REEL=True` sans `SSID` | `python main.py` | Refus de démarrage |
| `MODE_REEL=True` sans confirmation | `python main.py` | Refus, message sur la double confirmation |
| `MODE_REEL=True` complet | `python main.py` | Refus de démarrage (code 3), verrou d'exécution expliqué |
| `MONTANT_TRADE` > `TAILLE_MAX_POSITION` | démarrage | Refus de configuration |
| `EMA_RAPIDE` ≥ `EMA_LENTE` | démarrage | Refus de configuration |
| Perte journalière atteinte | laisser tourner en perdant | Ordres refusés, journal `avertissement`, bot vivant |
| Quota de trades atteint | `TRADES_MAX_PAR_JOUR=3` | Refus propre, aucun ordre supplémentaire |
| Deux ordres simultanés | appel direct `executer()` ×2 | `ExecutionRefusee` |
| Mise hors borne (0 ou 10 000 $) | `PUT /api/reglages` | HTTP 400, réglages inchangés |
| Mise > plafond de position | `PUT /api/reglages` | HTTP 400, cohérence refusée |
| Observation sans session | `PUT /api/reglages {"mode":"OBSERVATION"}` | HTTP 400, message sur le SSID |
| Mode réel demandé depuis la console | `PUT /api/reglages {"mode":"REEL"}` | HTTP 400, verrou expliqué |
| Jeton trop court | `POST /api/session {"ssid":"abc"}` | HTTP 400 |
| Inspecter `/api/session` | navigateur | Aucun secret : `apercu` masqué (`abcd…wxyz`) uniquement |
| Solde du compte | mode Observation sans adaptateur | L'UI affiche « indisponible », jamais un chiffre |
| Coupure réseau (réel) | couper le Wi-Fi | Backoff + reconnexion, pas de crash, état `RECONNEXION` |
| Fermeture de la console pendant un ordre | `Ctrl+C` | `arreter()` annule l'ordre, journalise, ne laisse rien ouvert |
| Inspecter `/api/etat` | navigateur | Aucun SSID, aucun secret, aucun token |

---

## 4. Validation de l'interface

- [ ] Les 6 KPI se mettent à jour en moins de 2 s après un trade.
- [ ] La courbe de capital coïncide avec `solde_apres` du CSV (vérifier les 3 derniers points).
- [ ] Les jauges de risque atteignent 100 % au moment exact du refus d'ordre.
- [ ] Le bouton pause fige la stratégie **et** l'exécution (aucun ordre pendant la pause).
- [ ] L'export CSV est identique au fichier `journal/trades.csv`.
- [ ] Aucune erreur dans la console navigateur (`F12`).
- [ ] Lisible à 1280 px et à 390 px de large (responsive).
- [ ] *Connexion supervisée* : le bouton « Se connecter avec votre navigateur » ouvre Edge, vous tapez, la console relit la session et affiche l'aperçu masqué sans jamais contenir le mot de passe.
- [ ] Après la connexion supervisée, `.audit/frames_capture_*.jsonl` contient des trames `42[...]` ; supprimer ce fichier après analyse.
- [ ] Si Edge refuse le canal : fallback possible en collant la session manuellement.
- [ ] *Réglages* : une borne invalide affiche le message d'erreur, les valeurs ne changent pas.
- [ ] *Réglages* : « Appliquer » met à jour les jauges de risque en direct (quota, plafond).
- [ ] *Connexion* : le jeton n'est jamais réaffiché en clair ; le champ se vide après « Garder en mémoire ».
- [ ] *Connexion* : « Effacer » rétablit le flux simulé et la pastille « session : aucune ».
- [ ] Les ancres `/#reglages` et `/#session` ouvrent le bon panneau depuis l'extérieur.

---

## 5. Critères go / no-go

Avant d'envisager un compte démo de plateforme :

- [ ] `pytest -q` : 100 % vert.
- [ ] 5 campagnes de 100 trades exécutées, **journalisées**, sans incohérence comptable.
- [ ] Zéro ordre malgré un `MODE_REEL=True` complet.
- [ ] Tous les tests négatifs passés.
- [ ] Reprise après coupure simulée vérifiée.

Et avant tout capital réel — condition **non technique** :

- [ ] Test sur compte démo plateforme pendant ≥ 4 semaines, avec le **même** code.
- [ ] Backtest sur données historiques réelles (le synthétique ne prouve rien).
      `scripts/backtest_historique.py --jours 30` : réussite, IC 95 %, P/L,
      walk-forward calibration/test — le résultat du mois de test seul compte.
- [ ] Mesure sur prix réels via `scripts/mesurer_flux_reel.py` (flux public Binance,
      lecture seule) : viser ≥ 100 trades avant toute conclusion — l'IC 95 % de
      100 trades reste large (± 10 points autour du taux observé).
- [ ] Walk-forward : la stratégie tient sur une période jamais utilisée pour la calibrer.
- [ ] Taille de position telle qu'une perte totale de la mise reste indolore.
- [ ] Acceptation écrite du risque : perte totale du capital possible.

Si une seule case reste vide : **on ne passe pas en réel.** Le mode réel n'est pas une
récompense, c'est une décision financière qui ne se prend pas sur la base de 100 trades.

---

## 6. Rapport type (à remplir)

```
Date : ................  Graine : ......  Trades : ......
Taux de réussite : ...... %   (seuil de rentabilité : 53,5 %)
Espérance / trade : ...... $  Drawdown max : ...... $
Ratio gain/perte : ......      Profit factor : ......
Série perdante max : ......    Refus de risque : ......
Incidents : .................................................
Conclusion : ❑ poursuivre la simulation  ❑ revoir la stratégie  ❑ arrêter
```
