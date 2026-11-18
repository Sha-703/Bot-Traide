# Architecture — Pocket Lab

## 1. Vue d'ensemble

Le système est organisé en **couches unidirectionnelles** : chaque module ne connaît
que celui du dessous. Une erreur d'exécution ne peut donc pas se propager dans la
stratégie, et une donnée de marché ne peut pas créer un ordre sans traverser le
contrôle de risque.

```
┌──────────────────────────────────────────────────────────────────────┐
│  TRANSVERSE (pilotage à chaud, rien sur disque)                      │
│  bot/reglages.py : la configuration vivante, validée côté serveur   │
│  bot/session.py  : le SSID en mémoire vive, aperçu masqué           │
│  bot/solde.py    : la provenance du solde affiché (simulé/réel)      │
└──────────────────────────────▲───────────────────────────────────────┘
                               │  API : 400 sur faute, jamais le jeton
┌───────────────────────────────┴─────────────────────────────────────┐
│  COUCHE PRÉSENTATION                                                 │
│  bot/serveur.py  (FastAPI, SSE, export CSV)                          │
│  bot/web/        (HTML/CSS/JS, graphiques SVG dessinés main)         │
└───────────────────────────────▲──────────────────────────────────────┘
                                │  instantané JSON (jamais le SSID)
┌───────────────────────────────┴──────────────────────────────────────┐
│  COUCHE ORCHESTRATION                                                │
│  bot/engine.py : boucle unique, état, alertes, abonnés SSE           │
└────┬───────────────┬────────────────┬─────────────────┬──────────────┘
     │               │                │                 │
┌────▼──────────┐ ┌──▼────────────┐ ┌─▼─────────────┐ ┌─▼─────────────┐
│ connexion.py  │ │ strategie.py  │ │  risque.py    │ │ execution.py  │
│ SourceDePrix  │ │ EMA + filtres │ │ Limites du    │ │ Verrous +     │
│ synthétique / │ │ Signal        │ │ jour, sizing  │ │ ordres papier │
│ WebSocket     │ │               │ │               │ │               │
└────┬──────────┘ └──────┬────────┘ └──────┬────────┘ └──────┬────────┘
     │                   │                 │                 │
     └───────────────────┴────────┬────────┴─────────────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │  journalisation.py         │
                    │  trades.csv + evenements   │
                    │  .jsonl + statistiques     │
                    └─────────────┬──────────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │  config.py (.env validé)   │
                    │  secrets jamais logués     │
                    └────────────────────────────┘
```

## 2. Rôles et contrats

### `config.py` — la source de vérité
- Lit `.env` via `python-dotenv` (`override=False` : le shell gagne).
- **Refuse de démarrer** si `MODE_REEL` est absent.
- Exige `SSID` **et** `CONFIRMATION_REELLE` en mode réel.
- Expose `Settings.resume_public()` : tout ce dont l'UI a besoin, sans secret.
- `Settings.ssid_masque()` ne laisse filtrer que 4 + 4 caractères.

### `connexion.py` — la source de prix
Deux implémentations d'une même interface `SourceDePrix` :

| Implémentation | Usage | Comportement |
|---|---|---|
| `FluxSynthetique` | démo, tests, CI | Marche aléatoire à régimes, graine reproductible, horloge virtuelle possible |
| `ConnexionPocketOption` | réel | WebSocket, auth par SSID, backoff exponentiel plafonné + jitter, 8 tentatives max |

Le parsing des messages est isolé dans `analyser_message()`. Le protocole Pocket Option
n'étant pas public, cette méthode est le **seul** point à adapter à une bibliothèque
tierce — et c'est précisément là qu'un audit de code est obligatoire (voir `SECURITY.md`).

### `strategie.py` — la décision
```
EMA_t = α · prix_t + (1 − α) · EMA_{t−1}        α = 2 / (période + 1)

croisement(signe(EMA_rapide − EMA_lente))
   → attente de confirmation : le signe doit se maintenir et |écart| > seuil
     (au tick exact du croisement l'écart vaut 0 : il ne peut pas servir de filtre)
   → cooldown global anti-whipsaw (aucun signal pendant N secondes)
   → Signal immuable
```
La stratégie est mise à jour **à chaque tick**, y compris pendant qu'un trade est ouvert :
sinon les EMA deviennent aveugles pendant l'expiration et le graphe présente des sauts.
Elle ne connaît ni le capital, ni le risque, ni le courtier. Elle émet un `Signal`
immuable avec sa justification textuelle (auditable).

### `risque.py` — le veto
`GestionRisque.valider(montant, capital)` vérifie, dans l'ordre :
1. montant > 0 ;
2. montant ≤ plafond de position ;
3. montant ≤ `fraction_risque_par_trade × capital` (budget recalculé à chaque trade) ;
4. capital suffisant ;
5. quota de trades du jour ;
6. perte journalière restante (reset automatique à minuit UTC).

Échec ⇒ `RisqueRefuse` : aucun ordre n'est créé, l'erreur est journalisée, le bot continue.

### `execution.py` — le passage à l'acte
Trois verrous, dans cet ordre :
1. **mode** — `MODE_REEL=True` ⇒ `ExecutionReelleBloquee` (aucun chemin réel livré) ;
2. **risque** — `risque.valider()` avant toute création d'ordre ;
3. **état** — un seul ordre en cours, pas de martingale, pas d'exposition multiple.

`CourtierDemo` résout l'ordre à l'échéance : gain = `montant × payout`, perte = `−montant`,
égalité = remboursement.

### `journalisation.py` — la preuve
- `journal/trades.csv` : une ligne par trade résolu, séparateur `;`, UTF-8.
- `journal/evenements.jsonl` : connexions, refus de risque, erreurs, heure UTC.
- `statistiques()` : taux de réussite, espérance, profit factor, drawdown maximal,
  courbe de solde (consommée directement par le graphique de la console).

### `reglages.py` — la configuration vivante
`.env` donne le point de départ ; la console modifie ensuite les valeurs **à
chaud**, sans redémarrer la boucle :

- une seule instance `moteur.reglages` existe et est mutée *en place* — les
  références détenues par `risque` et `execution` restent donc valides ;
- chaque mise à jour est re-validée : bornes strictes (mise ≤ 1 000 $, risque ≤ 10 %),
  cohérence (mise ≤ plafond, perte journalière ≥ une mise) ;
- les pourcentages arrivent en pourcentage (`fraction_risque_pct`) et sont
  convertis en fraction ; la virgule décimale est acceptée ;
- le mode est un champ comme les autres, sauf `REEL` qui est refusé (400).

Les gardes-fous modifiés (quota, plafond, perte) sont repoussés dans
`GestionRisque.limites` ; le payout dans `MoteurExecution.courtier`.

### `session.py` — la session plateforme en mémoire
- `GestionnaireSession` ne détient le SSID qu'en RAM (`repr=False`) ;
- `instantane()` n'expose que `apercu` = `abcd…wxyz` ;
- `tester()` ouvre un vrai WebSocket et distingue **serveur joignable**
  (handshake socket.io, reçu en < 6 s) de **jeton validé** — jamais d'invention :
  un handshake arrive avant toute authentification, donc `VALIDEE` est réservée
  à un futur adaptateur capable de vérifier le jeton ;
- effacement explicite (`oublier()`) ou à l'arrêt du serveur.

### `solde.py` — la provenance du solde affiché
`SoldeSimule` (capital papier) ou `SoldePlateforme` (lecture seule). Tant que
l'adaptateur n'existe pas, `SoldePlateforme.lire()` retourne `(None, explication)`
et l'interface affiche **« indisponible »** — jamais un chiffre crédible.
Basculer un jour en solde réel de compte = écrire un `lire()`, pas toucher l'UI.

## 3. Séquence d'un trade

```
Flux                 Engine              Strategie        Risque         Execution        Journal
 │  tick(prix, ts)    │                     │               │                │              │
 ├───────────────────>│                     │               │                │              │
 │                    ├──── maj(prix) ─────>│               │                │              │
 │                    │<──── Signal ? ──────┤               │                │              │
 │                    │  (si croisement)    │               │                │              │
 │                    ├──── budget/valider ─────────────────>│                │              │
 │                    │<──── ok / RisqueRefuse ──────────────┤                │              │
 │                    ├──── executer(signal, capital) ──────────────────────>│              │
 │                    │                     │               │   ordre ouvert ├─────────────>│
 │  tick(échéance)    │                     │               │                │              │
 ├───────────────────>│                                                      │              │
 │                    ├──── resoudre(prix_sortie) ──────────────────────────>│              │
 │                    │<──── (résultat, pnl, capital) ───────────────────────┤              │
 │                    ├──── enregistrer(trade) ───────────────────────────────────────────>│
 │                    ├──── _diffuser() ──> abonnés SSE (dashboard)                        │
```

## 4. Choix techniques assumés

| Décision | Raison |
|---|---|
| Boucle mono-tâche, un trade à la fois | Comportement déterministe, pas de course critique, backtest trivial. |
| Pas de pandas/numpy | Empreinte minimale, O(1) par tick, moins de surface d'attaque (dépendances). |
| Graphiques SVG faits main | Zéro CDN, fonctionne hors ligne, pas de script tiers dans la console. |
| SSE plutôt que WebSocket UI | Unidirectionnel, natif navigateur, aucune dépendance cliente. |
| `Moteur.instantane()` unique source | L'UI ne calcule rien : elle affiche. Une seule vérité. |
| `Journal` injectable | Tests isolés et campagnes multi-graines sans pollution des CSV. |

## 5. Points d'extension prévus

1. **Nouvelle stratégie** : implémenter `maj(prix, ts) -> Signal | None`, brancher dans `Moteur.__init__`.
2. **Nouvelle source de prix** : hériter de `SourceDePrix`, retourner des `Tick`.
3. **Nouveau courtier** : écrire un adaptateur respectant `ouvrir()` / `resoudre()`, puis
   remplacer `CourtierDemo` — après audit et tests sur compte démo plateforme.
   (Le solde affiché suit automatiquement : voir `solde.py`.)
4. **Backtest hors-ligne** : réutiliser `StrategieMoyennesMobiles` + `CourtierDemo` sur un
   fichier de prix historiques, sans démarrer le moteur.

## 6. Limites connues

- Le flux synthétique n'a **aucune** valeur prédictive : il teste la plomberie, pas l'edge.
- Une stratégie de suivi de tendance perd structurellement en marché plat (les frais/payout
  amplifient le phénomène : il faut ~53,5 % de réussite pour être à l'équilibre avec un payout de 87 %).
- Le protocole réel n'est pas public : la connexion WebSocket fournie est un squelette
  sérieux, pas une garantie de compatibilité. Un handshake socket.io reçu par « Tester
  la connexion » prouve seulement que le serveur répond — **pas** que le SSID est valide.
- Aucune gestion de la latence de la plateforme (le vrai risque caché du trading binaire).
