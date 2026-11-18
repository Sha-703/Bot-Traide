/* =========================================================================
   Pocket Lab — logique de la console
   - Flux temps réel via SSE (fallback polling)
   - Graphiques SVG dessinés à la main (aucune dépendance externe)
   ========================================================================= */

(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const nfMoney = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const nfInt = new Intl.NumberFormat("fr-FR");

  const fmtMoney = (v) => (v === null || v === undefined ? "—" : `${nfMoney.format(v)} $`);
  const fmtPrix = (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(5));
  const fmtPct = (v) => (v === null || v === undefined ? "—" : `${nfMoney.format(v)} %`);
  const signe = (v) => (v > 0 ? "+" : "");
  const heure = (iso) => {
    try {
      return new Date(iso).toLocaleTimeString("fr-FR", { hour12: false });
    } catch {
      return "—";
    }
  };

  let etat = null;
  let uptimeBase = { s: 0, t: Date.now() };
  let dernierStatut = null;

  /* ------------------------------------------------------------------ */
  /* Graphiques                                                          */
  /* ------------------------------------------------------------------ */
  function svgEl(tag, attrs = {}) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  }

  function chemin(points) {
    return points.map((p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
  }

  function dessiner(svg, series, opts = {}) {
    const box = svg.parentElement;
    const W = Math.max(280, box.clientWidth);
    const H = Math.max(140, box.clientHeight);
    const padL = opts.padL ?? 46;
    const padR = opts.padR ?? 12;
    const padT = opts.padT ?? 14;
    const padB = opts.padB ?? 22;
    const yFmt = opts.yFmt ?? ((v) => nfInt.format(Math.round(v)));

    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.replaceChildren();

    const toutes = series.flatMap((s) => s.values.filter((v) => Number.isFinite(v)));
    if (opts.reference !== undefined && opts.reference !== null) toutes.push(opts.reference);
    if (toutes.length < 2) return false;

    let min = Math.min(...toutes);
    let max = Math.max(...toutes);
    if (max - min < 1e-9) { max += 1; min -= 1; }
    const marge = (max - min) * 0.12;
    min -= marge; max += marge;

    const x = (i, n) => padL + (i * (W - padL - padR)) / Math.max(1, n - 1);
    const y = (v) => padT + ((max - v) / (max - min)) * (H - padT - padB);

    const defs = svgEl("defs");
    svg.appendChild(defs);

    // Grille horizontale + labels
    const grad = svgEl("linearGradient", { id: "gridfade", x1: "0", y1: "0", x2: "0", y2: "1" });
    for (let i = 0; i <= 4; i++) {
      const v = min + ((max - min) * i) / 4;
      const yy = y(v);
      svg.appendChild(
        svgEl("line", {
          x1: padL, x2: W - padR, y1: yy, y2: yy,
          stroke: "#1a2431", "stroke-width": "1", "stroke-dasharray": i === 0 || i === 4 ? "0" : "3 5",
        })
      );
      const label = svgEl("text", {
        x: padL - 8, y: yy + 3.5, "text-anchor": "end",
        fill: "#5f7086", "font-size": "9.5", "font-family": "var(--mono)",
      });
      label.textContent = yFmt(v);
      svg.appendChild(label);
    }

    // Ligne de référence (capital initial)
    if (opts.reference !== undefined && opts.reference !== null) {
      svg.appendChild(
        svgEl("line", {
          x1: padL, x2: W - padR, y1: y(opts.reference), y2: y(opts.reference),
          stroke: "#7d8ba0", "stroke-width": "1", "stroke-dasharray": "4 5", opacity: "0.55",
        })
      );
    }

    // Séries
    series.forEach((s, idx) => {
      const n = s.values.length;
      const pts = s.values.map((v, i) => [x(i, n), y(v)]);

      if (s.fill && n > 1) {
        const gid = `grad-${s.fill}`;
        const g = svgEl("linearGradient", { id: gid, x1: "0", y1: "0", x2: "0", y2: "1" });
        const stop1 = svgEl("stop", { offset: "0", "stop-color": s.color, "stop-opacity": "0.30" });
        const stop2 = svgEl("stop", { offset: "1", "stop-color": s.color, "stop-opacity": "0" });
        g.append(stop1, stop2);
        defs.appendChild(g);
        const aire = `${chemin(pts)}L${x(n - 1, n).toFixed(1)},${(H - padB).toFixed(1)}L${padL.toFixed(1)},${(H - padB).toFixed(1)}Z`;
        svg.appendChild(svgEl("path", { d: aire, fill: `url(#${gid})`, stroke: "none" }));
      }

      const p = svgEl("path", {
        d: chemin(pts),
        fill: "none",
        stroke: s.color,
        "stroke-width": s.width ?? 1.8,
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
        opacity: s.opacity ?? 1,
      });
      if (s.dashed) p.setAttribute("stroke-dasharray", s.dashed);
      if (s.glow) p.style.filter = `drop-shadow(0 0 5px ${s.color}88)`;
      svg.appendChild(p);

      if (s.marker && n > 0) {
        const [lx, ly] = pts[n - 1];
        svg.appendChild(svgEl("circle", { cx: lx, cy: ly, r: 6, fill: s.color, opacity: "0.16" }));
        svg.appendChild(svgEl("circle", { cx: lx, cy: ly, r: 2.6, fill: s.color }));
      }
    });

    // Repères X
    if (opts.xLabels !== false) {
      const n = Math.max(...series.map((s) => s.values.length));
      const t1 = svgEl("text", { x: padL, y: H - 6, fill: "#5f7086", "font-size": "9.5", "font-family": "var(--mono)" });
      t1.textContent = "début";
      const t2 = svgEl("text", { x: W - padR, y: H - 6, "text-anchor": "end", fill: "#5f7086", "font-size": "9.5", "font-family": "var(--mono)" });
      t2.textContent = `${n} points`;
      svg.append(t1, t2);
    }

    svg._meta = { padL, padR, W, H, min, max, series };
    return true;
  }

  function dessinerCourbe(instantane) {
    const courbe = instantane.statistiques.courbe_solde.map((p) => p.solde);
    const wrap = $("wrap-equity");
    const ok = courbe.length >= 2;
    $("empty-equity").hidden = ok;
    if (!ok) { $("chart-equity").replaceChildren(); return; }

    // Précision des libellés selon l'amplitude : sinon « 1 000,00 » se répète
    // trois fois sur une courbe qui oscille de quelques dollars.
    const toutes = [...courbe, instantane.capital_initial];
    const etendue = Math.max(...toutes) - Math.min(...toutes);
    const decimales = etendue < 3 ? 2 : etendue < 30 ? 1 : 0;
    const formater = (v) =>
      Number(v).toLocaleString("fr-FR", {
        minimumFractionDigits: decimales,
        maximumFractionDigits: decimales,
      });

    dessiner($("chart-equity"), [
      { values: courbe, color: "#34e5a3", width: 2, fill: "equity", glow: true, marker: true },
    ], {
      reference: instantane.capital_initial,
      padL: decimales === 2 ? 66 : 56,
      yFmt: formater,
    });
    void wrap;
  }

  function dessinerPrix(instantane) {
    const s = instantane.strategie;
    const prix = s.prix || [];
    const ok = prix.length >= 2;
    $("empty-price").hidden = ok;
    if (!ok) { $("chart-price").replaceChildren(); return; }
    const valeurs = [...prix, ...(s.ema_r || []), ...(s.ema_l || [])];
    const etendue = Math.max(...valeurs) - Math.min(...valeurs);
    // Précision adaptative : sans cela, deux libellés voisins deviennent identiques.
    const decimales = etendue < 0.0015 ? 5 : 4;
    dessiner($("chart-price"), [
      { values: prix, color: "#cfe0f5", width: 1.4, fill: "prix", opacity: 0.9 },
      { values: s.ema_l || [], color: "#ffb648", width: 1.5 },
      { values: s.ema_r || [], color: "#6ea8ff", width: 1.6, marker: true },
    ], { xLabels: false, yFmt: (v) => v.toFixed(decimales), padL: 50 });
  }

  // Infobulle sur la courbe de capital
  (() => {
    const svg = $("chart-equity");
    const tip = $("tip-equity");
    svg.addEventListener("mousemove", (ev) => {
      const meta = svg._meta;
      if (!meta) return;
      const rect = svg.getBoundingClientRect();
      const px = ev.clientX - rect.left;
      const values = meta.series[0].values;
      const n = values.length;
      const ratio = (px - meta.padL) / Math.max(1, meta.W - meta.padL - meta.padR);
      const i = Math.max(0, Math.min(n - 1, Math.round(ratio * (n - 1))));
      const xx = meta.padL + (i * (meta.W - meta.padL - meta.padR)) / Math.max(1, n - 1);
      const yy = meta.H - (meta.H - 0) * 0; // recalcul propre ci-dessous
      const yPixel =
        14 + ((meta.max - values[i]) / (meta.max - meta.min)) * (meta.H - 14 - 22);
      tip.hidden = false;
      tip.innerHTML = `<b>${fmtMoney(values[i])}</b><br><small style="color:#75869c">trade #${i + 1}</small>`;
      tip.style.left = `${(xx / meta.W) * rect.width}px`;
      tip.style.top = `${(yPixel / meta.H) * rect.height}px`;
      void yy;
    });
    svg.addEventListener("mouseleave", () => { tip.hidden = true; });
  })();

  /* ------------------------------------------------------------------ */
  /* Rendu du reste de l'interface                                       */
  /* ------------------------------------------------------------------ */
  function rendreKpis(s) {
    const st = s.statistiques;
    const solde = s.solde || { valeur: s.capital, origine: "simulé", note: "" };
    $("kpi-capital").textContent = fmtMoney(solde.valeur);
    const sousTitre =
      solde.origine === "plateforme"
        ? "solde du compte · lecture seule"
        : solde.origine === "mt5_demo"
          ? "compte démo MT5 · lecture seule"
          : solde.origine === "indisponible"
            ? "solde du compte indisponible"
            : `papier · initial ${fmtMoney(s.capital_initial)}`;
    $("kpi-capital-sub").textContent = sousTitre;
    $("kpi-capital-sub").title =
      (solde.note || "") +
      " Capital papier du bot : le flux de prix (synthétique ou Binance) ne " +
      "fournit aucun solde. Aucun compte Binance n'est connecté.";

    const pnl = $("kpi-pnl");
    pnl.textContent = `${signe(s.pnl_session)}${fmtMoney(s.pnl_session)}`;
    pnl.className = `kpi-value mono ${s.pnl_session > 0 ? "pos" : s.pnl_session < 0 ? "neg" : ""}`;
    $("kpi-pnl-sub").textContent = `${signe(s.pct_session)}${fmtPct(s.pct_session)} du capital`;

    $("kpi-taux").textContent = st.total ? `${st.taux_reussite.toFixed(1).replace(".", ",")} %` : "—";
    $("kpi-taux-sub").textContent = `${st.gagnants} gagnant(s) / ${st.perdants} perdant(s)`;

    $("kpi-trades").textContent = nfInt.format(st.total);
    $("kpi-trades-sub").textContent = `${s.risque.trades_du_jour} / ${s.risque.trades_max} aujourd'hui`;

    $("kpi-dd").textContent = st.drawdown_max > 0 ? fmtMoney(-st.drawdown_max) : fmtMoney(0);
    $("kpi-rr").textContent = st.ratio_gain_perte ? `1 : ${nfMoney.format(st.ratio_gain_perte)}` : "—";
    $("kpi-expectance").textContent = `espérance ${signe(st.esperance)}${fmtMoney(st.esperance)} / trade`;
  }

  function rendreRisque(s) {
    const r = s.risque;
    $("risk-jour").textContent = r.jour || "—";
    $("risk-perte-label").textContent = `${fmtMoney(r.perte_consommee)} / ${fmtMoney(r.perte_max_journaliere)}`;
    const bar = $("risk-perte-bar");
    bar.style.width = `${Math.min(100, r.pct_perte_utilisee)}%`;
    bar.parentElement.className = `bar ${r.pct_perte_utilisee > 75 ? "danger" : ""}`;
    $("risk-perte-hint").textContent =
      r.pct_perte_utilisee > 75
        ? "Zone critique : arrêt automatique imminent."
        : r.perte_consommee > 0
          ? `Reste ${fmtMoney(r.perte_restante)} de marge de perte.`
          : "Aucune perte engagée.";

    $("risk-trades-label").textContent = `${r.trades_du_jour} / ${r.trades_max}`;
    const box = $("risk-segments");
    const cellules = Math.min(r.trades_max, 24);
    if (box.childElementCount !== cellules) {
      box.replaceChildren(...Array.from({ length: cellules }, () => document.createElement("i")));
    }
    [...box.children].forEach((cell, i) => {
      const utilise = i < Math.min(r.trades_du_jour, cellules);
      cell.className = utilise ? `used${r.trades_du_jour / r.trades_max > 0.85 ? " danger" : r.trades_du_jour / r.trades_max > 0.6 ? " warn" : ""}` : "";
    });

    $("risk-budget").textContent = fmtMoney(r.budget_trade);
    $("risk-plafond").textContent = fmtMoney(s.config.taille_max_position);
    $("risk-fraction").textContent = fmtPct(s.config.fraction_risque_par_trade * 100);
    $("risk-serie").textContent = `${r.series_perdantes} (max ${r.serie_perdante_max})`;
  }

  function rendreSignal(s) {
    const st = s.strategie;
    const d = s.dernier_signal;
    const chip = $("signal-chip");
    chip.textContent = d ? d.direction : "NEUTRE";
    chip.dataset.dir = d ? d.direction : "NEUTRE";
    $("signal-raison").textContent = d ? d.raison : "En attente d'un croisement validé…";
    $("signal-prix").textContent = st.dernier_prix ? fmtPrix(st.dernier_prix) : "—";
    $("signal-ecart").textContent = nfMoney.format(st.ecart_bps ?? 0);
    $("signal-tendance").textContent = st.tendance;
    $("ema-r").textContent = st.ema_rapide ? fmtPrix(st.ema_rapide) : "—";
    $("ema-l").textContent = st.ema_lente ? fmtPrix(st.ema_lente) : "—";
    $("legend-fast").textContent = `EMA ${s.config.ema_rapide}`;
    $("legend-slow").textContent = `EMA ${s.config.ema_lente}`;

    // Indique clairement pourquoi aucun signal n'apparaît au démarrage.
    const chauffe = $("chauffe");
    const pret = st.points_chauffe >= st.points_requis && st.points_requis > 0;
    chauffe.textContent = pret
      ? "· stratégie prête"
      : `· chauffe ${st.points_chauffe}/${st.points_requis} points`;
    chauffe.className = `chauffe${pret ? " pret" : ""}`;

    const carte = $("order-card");
    const o = s.ordre_en_cours;
    carte.hidden = !o;
    if (o) {
      $("order-meta").textContent = `${o.montant.toFixed(2)} $ · entrée ${fmtPrix(o.prix_entree)}`;
      $("order-dir").textContent = o.direction;
      $("order-dir").dataset.dir = o.direction;
      $("order-bar").style.width = `${Math.round(o.progression * 100)}%`;
      $("order-countdown").textContent = `${Math.ceil(o.restant_s)} s`;
      $("order-entry").textContent = o.identifiant;
    }
  }

  function rendreTrades(s) {
    const tbody = $("rows-trades");
    const trades = s.trades || [];
    $("table-count").textContent = nfInt.format(s.statistiques.total);
    if (!trades.length) {
      tbody.innerHTML = `<tr class="placeholder"><td colspan="9">Aucun trade pour le moment — premier signal après la chauffe des moyennes mobiles.</td></tr>`;
      return;
    }
    tbody.innerHTML = trades
      .map((t) => {
        const cls = t.pnl > 0 ? "pos" : t.pnl < 0 ? "neg" : "";
        return `<tr>
          <td class="mono">${heure(t.horodatage_utc)}</td>
          <td>${t.actif}</td>
          <td><span class="dir" data-dir="${t.direction}">${t.direction}</span></td>
          <td class="num mono">${fmtMoney(t.montant)}</td>
          <td class="num mono">${fmtPrix(t.prix_entree)}</td>
          <td class="num mono">${fmtPrix(t.prix_sortie)}</td>
          <td><span class="res" data-res="${t.resultat}">${t.resultat}</span></td>
          <td class="num mono pnl ${cls}">${signe(t.pnl)}${fmtMoney(t.pnl)}</td>
          <td class="num mono">${fmtMoney(t.solde_apres)}</td>
        </tr>`;
      })
      .join("");
  }

  function rendreFlux(s) {
    const ul = $("feed");
    const alertes = s.alertes || [];
    if (!alertes.length) {
      ul.innerHTML = `<li class="feed-empty">Aucun événement pour l'instant.</li>`;
      return;
    }
    ul.innerHTML = alertes
      .map(
        (a) => `<li data-niveau="${a.niveau}">
          <time>${new Date(a.ts * 1000).toLocaleTimeString("fr-FR", { hour12: false })}</time>
          <span>${a.message}</span>
        </li>`
      )
      .join("");
  }

  function rendreStatut(s) {
    const pill = $("pill-mode");
    pill.dataset.mode = s.mode;
    const libellesMode = { DEMO: "DÉMO", OBSERVATION: "OBSERVATION", REEL: "RÉEL" };
    pill.querySelector("b").textContent = libellesMode[s.mode] || s.mode;

    const conn = $("pill-connexion");
    conn.dataset.etat = s.connexion.etat;
    const libelles = {
      CONNECTE: "flux connecté",
      CONNEXION: "connexion…",
      RECONNEXION: "reconnexion…",
      DECONNECTE: "déconnecté",
      ERREUR: "erreur réseau",
    };
    // Le nom de la source est affiché : « flux connecté » seul ne dit pas si
    // les prix sont réels ou générés. C'est la question n°1 de l'opérateur.
    const nomsSource = {
      binance: "Binance · prix réels",
      synthetique: "synthétique · prix simulés",
      pocket_option: "Pocket Option · prix réels",
    };
    const nomSource = nomsSource[s.connexion.source];
    conn.querySelector("span").textContent =
      (libelles[s.connexion.etat] || s.connexion.etat) +
      (nomSource ? ` · ${nomSource}` : "");
    conn.title = nomSource
      ? nomSource.startsWith("synthétique")
        ? "Prix générés par le simulateur : ce ne sont pas des prix de marché."
        : "Prix de marché réels, lus en lecture seule. Les ordres restent papier."
      : "";
    $("pill-actif").textContent = s.actif;

    uptimeBase = { s: s.uptime_s, t: Date.now() };
    majUptime();

    const btn = $("btn-pause");
    const statut = s.statut;
    btn.textContent =
      statut === "EN_PAUSE"
        ? "Reprendre"
        : statut === "EN_COURS"
          ? "Mettre en pause"
          : "Démarrer";
    btn.dataset.actif = statut === "EN_PAUSE" ? "true" : "false";
    btn.disabled = false;

    // Une campagne terminée mérite un rechargement (compteurs remis à zéro).
    // En revanche, une erreur réseau persistante ne doit pas boucler : elle
    // reste affichée dans le flux jusqu'à une action de l'opérateur.
    if (s.statut !== dernierStatut && s.statut === "TERMINE") {
      setTimeout(() => location.reload(), 4000);
    }
    dernierStatut = s.statut;
  }

  function majUptime() {
    const total = uptimeBase.s + Math.floor((Date.now() - uptimeBase.t) / 1000);
    const h = String(Math.floor(total / 3600)).padStart(2, "0");
    const m = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
    const sec = String(total % 60).padStart(2, "0");
    $("pill-uptime").textContent = `${h}:${m}:${sec}`;
  }

  function rendre(s) {
    etat = s;
    rendreStatut(s);
    rendreKpis(s);
    rendreRisque(s);
    rendreSignal(s);
    rendreTrades(s);
    rendreFlux(s);
    dessinerCourbe(s);
    dessinerPrix(s);
  }

  /* ------------------------------------------------------------------ */
  /* Réseau                                                              */
  /* ------------------------------------------------------------------ */
  let source = null;
  let polling = null;

  function demarrerPolling() {
    if (polling) return;
    polling = setInterval(async () => {
      try {
        const r = await fetch("/api/etat", { cache: "no-store" });
        if (r.ok) rendre(await r.json());
      } catch { /* serveur momentanément indisponible */ }
    }, 2500);
  }

  function connecter() {
    if (source) source.close();
    source = new EventSource("/api/stream");
    source.onmessage = (ev) => {
      try {
        if (polling) { clearInterval(polling); polling = null; }
        rendre(JSON.parse(ev.data));
      } catch (e) {
        console.warn("Message SSE illisible", e);
      }
    };
    source.onerror = () => {
      demarrerPolling();
      // EventSource se reconnecte seul ; on force une nouvelle instance
      // si le flux reste muet trop longtemps.
      setTimeout(() => {
        if (source && source.readyState === EventSource.CLOSED) connecter();
      }, 3000);
    };
  }

  /* ------------------------------------------------------------------ */
  /* Contrôles                                                           */
  /* ------------------------------------------------------------------ */
  async function controler(action) {
    try {
      await fetch("/api/controle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
    } catch (e) {
      console.error(e);
    }
  }

  $("btn-pause").addEventListener("click", () => {
    const statut = etat?.statut;
    if (statut === "EN_COURS") controler("pause");
    else if (statut === "EN_PAUSE") controler("reprendre");
    else controler("demarrer");
  });

  $("btn-campagne").addEventListener("click", async () => {
    const ok = window.confirm(
      "Archiver le journal et repartir de zéro ?\n\n" +
        "Le fichier journal/trades.csv est déplacé dans journal/campagnes/ " +
        "(rien n'est supprimé). Le bot est arrêté, le solde repart au capital " +
        "initial et les compteurs du jour sont remis à zéro."
    );
    if (!ok) return;
    const note = $("campagne-note");
    try {
      const r = await fetch("/api/campagne", { method: "POST" });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Erreur serveur");
      note.textContent = data.archive
        ? `Archivé : ${data.archive} · solde ${data.capital.toFixed(2)} $ — cliquez sur Démarrer.`
        : `Journal déjà vide · compteurs remis à zéro (${data.capital.toFixed(2)} $) — cliquez sur Démarrer.`;
      const etatMaj = await fetch("/api/etat", { cache: "no-store" });
      if (etatMaj.ok) rendre(await etatMaj.json());
    } catch (e) {
      note.textContent = `Échec : ${e.message}`;
    }
  });

  $("btn-reset").addEventListener("click", async () => {
    const ok = window.confirm(
      "Tout réinitialiser ?\n\n" +
        "• le journal est archivé dans journal/campagnes/ (rien n'est supprimé)\n" +
        "• le solde repart au capital initial\n" +
        "• les compteurs du jour sont remis à zéro\n" +
        "• tous les réglages reviennent aux valeurs du fichier .env\n" +
        "• le bot est arrêté"
    );
    if (!ok) return;
    const note = $("campagne-note");
    try {
      const r = await fetch("/api/reinitialiser", { method: "POST" });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Erreur serveur");
      const [etatMaj, reglagesMaj] = await Promise.all([
        fetch("/api/etat", { cache: "no-store" }),
        fetch("/api/reglages", { cache: "no-store" }),
      ]);
      if (etatMaj.ok) rendre(await etatMaj.json());
      if (reglagesMaj.ok) remplirReglages(await reglagesMaj.json());
      note.textContent =
        (data.archive ? `Archivé : ${data.archive} · ` : "") +
        `réglages .env restaurés · solde ${data.capital.toFixed(2)} $ · ` +
        `flux ${data.flux} (${data.actif}) — cliquez sur Démarrer.`;
    } catch (e) {
      note.textContent = `Échec : ${e.message}`;
    }
  });

  window.addEventListener("resize", () => {
    if (!etat) return;
    dessinerCourbe(etat);
    dessinerPrix(etat);
  });

  /* ------------------------------------------------------------------ */
  /* Fenêtres : réglages et connexion                                    */
  /* ------------------------------------------------------------------ */
  let modeChoisi = "DEMO";
  let fluxChoisi = "simule";

  function afficherMessage(id, texte) {
    const el = $(id);
    if (!el) return;
    el.hidden = !texte;
    el.textContent = texte || "";
  }

  function ouvrirDialogue(id) {
    const d = $(id);
    if (!d) return;
    try {
      if (!d.open) d.showModal();
    } catch {
      d.setAttribute("open", "");
    }
  }

  document.querySelectorAll("[data-fermer]").forEach((b) => {
    b.addEventListener("click", () => {
      const d = b.closest("dialog");
      if (d) d.close();
    });
  });

  document.querySelectorAll("dialog.modal").forEach((d) => {
    d.addEventListener("click", (ev) => {
      if (ev.target === d) d.close(); // clic sur le fond uniquement
    });
  });

  /* --- Réglages (mise, garde-fous, mode) ----------------------------- */
  function majSegments() {
    $("rg-modes").querySelectorAll(".seg").forEach((b) => {
      const actif = b.dataset.mode === modeChoisi;
      b.classList.toggle("actif", actif);
      b.setAttribute("aria-checked", actif ? "true" : "false");
    });
    const notes = {
      DEMO: "Flux synthétique ou MT5 (.env) : aucun ordre réel.",
      OBSERVATION:
        "Indisponible : cette option reliait Pocket Option. Pas de bridge MT5 pour l'instant — l'option passera par le panneau MT5.",
      REEL: "Verrouillé : aucun adaptateur d'exécution réelle n'est livré avec ce projet.",
    };
    $("rg-note").textContent = notes[modeChoisi] || "";
  }

  function majFlux() {
    $("rg-flux").querySelectorAll(".seg").forEach((b) => {
      const actif = b.dataset.flux === fluxChoisi;
      b.classList.toggle("actif", actif);
      b.setAttribute("aria-checked", actif ? "true" : "false");
    });
    $("rg-flux-note").textContent =
      fluxChoisi === "binance"
        ? "Prix réels publics Binance (BTC/USDT). Lecture seule : aucun compte requis, capital papier."
        : "Générateur interne : aucun réseau, aucun compte. Les résultats ne décrivent pas le marché.";
  }

  async function rendreFluxActif() {
    const el = $("rg-flux-actif");
    try {
      const r = await fetch("/api/mt5", { cache: "no-store" });
      const d = r.ok ? await r.json() : null;
      if (d && d.ouvert) {
        el.innerHTML = `Flux actif : <b>MT5 · ${d.symbole}</b> — ticks réels du compte démo (lecture seule, aucun ordre).`;
        el.classList.add("actif");
      } else {
        el.innerHTML = `Flux actif : <b>simulation</b> — activez <code>MT5_DEMO=true</code> dans <code>.env</code> + redémarrez pour utiliser MT5.`;
        el.classList.remove("actif");
      }
    } catch {
      el.textContent = "Flux actif : inconnu (serveur injoignable).";
    }
  }

  function remplirReglages(r) {
    if (!r) return;
    modeChoisi = r.mode || "DEMO";
    fluxChoisi = r.flux_prix || "simule";
    $("rg-montant").value = r.montant_trade;
    $("rg-plafond").value = r.taille_max_position;
    $("rg-fraction").value = r.fraction_risque_pct;
    $("rg-perte").value = r.perte_max_journaliere;
    $("rg-trades").value = r.trades_max_par_jour;
    $("rg-expiration").value = r.duree_expiration_s;
    $("rg-payout").value = r.payout_pct;
    majSegments();
    majFlux();
    rendreFluxActif();
    afficherMessage("rg-erreur", "");
    afficherMessage("rg-ok", "");
  }

  async function chargerReglages() {
    try {
      const r = await fetch("/api/reglages", { cache: "no-store" });
      if (r.ok) remplirReglages(await r.json());
    } catch { /* console momentanément hors ligne */ }
  }

  $("rg-modes").addEventListener("click", (ev) => {
    const bouton = ev.target.closest(".seg");
    if (!bouton) return;
    const mode = bouton.dataset.mode;
    if (mode === "REEL") {
      afficherMessage(
        "rg-erreur",
        "Mode réel verrouillé : aucun adaptateur d'exécution n'est livré. " +
          "Utilisez l'observation (données réelles, ordres papier) ou écrivez un courtier audité."
      );
      return;
    }
    modeChoisi = mode;
    afficherMessage("rg-erreur", "");
    afficherMessage("rg-ok", "");
    majSegments();
  });

  $("rg-flux").addEventListener("click", (ev) => {
    const bouton = ev.target.closest(".seg");
    if (!bouton) return;
    fluxChoisi = bouton.dataset.flux;
    afficherMessage("rg-erreur", "");
    afficherMessage("rg-ok", "");
    majFlux();
  });

  $("rg-enregistrer").addEventListener("click", async () => {
    const corps = {
      montant_trade: $("rg-montant").value,
      taille_max_position: $("rg-plafond").value,
      fraction_risque_pct: $("rg-fraction").value,
      perte_max_journaliere: $("rg-perte").value,
      trades_max_par_jour: $("rg-trades").value,
      duree_expiration_s: $("rg-expiration").value,
      payout_pct: $("rg-payout").value,
      mode: modeChoisi,
      flux_prix: fluxChoisi,
    };
    try {
      const r = await fetch("/api/reglages", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(corps),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        afficherMessage("rg-ok", "");
        afficherMessage("rg-erreur", data.detail || "Réglages refusés.");
        return;
      }
      // Remplir AVANT d'afficher le message : remplirReglages() nettoie les
      // bandeaux, et écraserait la confirmation qu'on vient de poser.
      if (data.reglages) remplirReglages(data.reglages);
      afficherMessage("rg-erreur", "");
      afficherMessage("rg-ok", data.message || "Réglages appliqués.");
      chargerMt5();
    } catch {
      afficherMessage("rg-erreur", "Serveur injoignable : réglages non appliqués.");
    }
  });

  $("rg-reset").addEventListener("click", async () => {
    try {
      const r = await fetch("/api/reglages/defauts", { cache: "no-store" });
      if (!r.ok) return;
      remplirReglages(await r.json());
      afficherMessage("rg-ok", "Valeurs du .env chargées : cliquez Appliquer pour les activer.");
    } catch { /* ignoré */ }
  });

  $("btn-reglages").addEventListener("click", async () => {
    await chargerReglages();
    ouvrirDialogue("modal-reglages");
  });

  /* --- Compte Weltrade MT5 (panneau de connexion) --------------------- */
  function rendreMt5(data) {
    if (!data) return;
    const configurer = !!data.configure;
    const ouvert = !!data.ouvert;
    const pill = $("pill-session");
    pill.dataset.etat = ouvert ? "EN_MEMOIRE" : configurer ? "ECHEC" : "VIDE";
    const libelles = {
      EN_MEMOIRE: `MT5 · #${data.login ?? "—"} · ${data.serveur ?? "—"}`,
      ECHEC: "MT5 · terminal introuvable",
      VIDE: "MT5 · non configuré (MT5_DEMO)",
    };
    pill.querySelector("span").textContent = libelles[pill.dataset.etat] || "MT5";
    pill.title = data.note || "";

    const pos = (id, v) => {
      const el = $(id);
      if (el) el.textContent = v ?? "—";
    };
    pos("mt5-terminal", ouvert ? "connecté ✓" : "indisponible");
    pos("mt5-serveur", data.serveur);
    pos("mt5-login", data.login != null ? `#${data.login}` : "—");
    pos("mt5-nom", data.nom);
    pos("mt5-devise", data.devise);
    pos("mt5-levier", data.levier ? `1:${data.levier}` : "—");
    pos("mt5-solde", data.balance != null ? `${data.balance} ${data.devise || ""}` : "—");
    pos("mt5-equity", data.equity != null ? `${data.equity} ${data.devise || ""}` : "—");

    const note = $("mt5-note");
    if (!configurer) {
      note.textContent = "Activez MT5_DEMO=true dans `.env`, puis redémarrez la console.";
    } else if (!ouvert) {
      note.textContent =
        data.note || "Le terminal Weltrade MT5 n'est pas démarré ou aucun compte n'est connecté.";
    } else {
      note.textContent =
        data.note || `Flux mt5_demo · ${data.symbole} · compte démo #${data.login}`;
    }
  }

  async function chargerMt5() {
    try {
      const r = await fetch("/api/mt5", { cache: "no-store" });
      if (r.ok) rendreMt5(await r.json());
    } catch { /* ignoré */ }
  }

  $("mt5-rafraichir").addEventListener("click", async () => {
    afficherMessage("se-erreur", "");
    await chargerMt5();
  });

  $("btn-connexion").addEventListener("click", async () => {
    await chargerMt5();
    ouvrirDialogue("modal-session");
  });

  /* ------------------------------------------------------------------ */
  /* Démarrage                                                           */
  /* ------------------------------------------------------------------ */
  chargerMt5();
  chargerReglages();
  const ouvrirDepuisFragment = () => {
    if (location.hash === "#mt5") ouvrirDialogue("modal-session");
    if (location.hash === "#reglages") ouvrirDialogue("modal-reglages");
  };
  ouvrirDepuisFragment();
  window.addEventListener("hashchange", ouvrirDepuisFragment);

  setInterval(majUptime, 1000);
  connecter();
  demarrerPolling();
})();
