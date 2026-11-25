const state = {
  selectedSymbol: "XAUUSD",
  selectedTimeframe: "M15",
  data: null,
  activeChart: null,
  serverFilter: "ALL",
  showEmas: true,
  lastSeriesKey: "",
  // TradingView Lightweight Charts instances
  tvChart: null,
  candleSeries: null,
  ema9Series: null,
  ema21Series: null,
  ema50Series: null,
  priceLines: [],
  lastBar: null,
};

async function api(url, method = "GET", body = null) {
  const opts = { method, headers: {} };
  if (body !== null) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  return await res.json();
}

function showBanner(msg, type = "info") {
  const el = document.getElementById("status-banner");
  el.textContent = msg;
  el.className = `status-banner ${type}`;
}

function fmtPrice(sym, val) {
  if (val === null || val === undefined || isNaN(val)) return "--";
  if (sym.includes("XAU")) return Number(val).toFixed(2);
  if (sym.includes("JPY")) return Number(val).toFixed(3);
  return Number(val).toFixed(5);
}

function getPricePrecision(sym) {
  if (sym.includes("XAU")) return { precision: 2, minMove: 0.01 };
  if (sym.includes("JPY")) return { precision: 3, minMove: 0.001 };
  return { precision: 5, minMove: 0.00001 };
}

function signalBadge(sig) {
  if (sig === "BUY") return `<span class="badge badge-buy">BUY</span>`;
  if (sig === "SELL") return `<span class="badge badge-sell">SELL</span>`;
  return `<span class="badge badge-hold">HOLD</span>`;
}

function toUnixSec(isoStr) {
  const ms = Date.parse(isoStr);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : Math.floor(Date.now() / 1000);
}

function ensureTvChart() {
  if (state.tvChart || typeof LightweightCharts === "undefined") return;
  const container = document.getElementById("tv-chart");
  const chart = LightweightCharts.createChart(container, {
    width: container.clientWidth || 880,
    height: 420,
    layout: {
      background: { type: "solid", color: "#0b0f17" },
      textColor: "#9fb0c9",
      fontSize: 12,
    },
    grid: {
      vertLines: { color: "#151e2e" },
      horzLines: { color: "#151e2e" },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: "#475569", width: 1, style: 2, labelBackgroundColor: "#1e293b" },
      horzLine: { color: "#475569", width: 1, style: 2, labelBackgroundColor: "#1e293b" },
    },
    rightPriceScale: {
      borderColor: "#253149",
      scaleMargins: { top: 0.1, bottom: 0.12 },
    },
    timeScale: {
      borderColor: "#253149",
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 6,
      barSpacing: 8,
    },
    handleScroll: {
      mouseWheel: true,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: false,
    },
    handleScale: {
      axisPressedMouseMove: true,
      mouseWheel: true,
      pinch: true,
    },
  });

  state.candleSeries = chart.addCandlestickSeries({
    upColor: "#22c55e",
    downColor: "#ef4444",
    borderUpColor: "#22c55e",
    borderDownColor: "#ef4444",
    wickUpColor: "#22c55e",
    wickDownColor: "#ef4444",
  });

  state.ema9Series = chart.addLineSeries({ color: "#38bdf8", lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });
  state.ema21Series = chart.addLineSeries({ color: "#f5b400", lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });
  state.ema50Series = chart.addLineSeries({ color: "#a855f7", lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });

  chart.subscribeCrosshairMove((param) => {
    const leg = document.getElementById("tv-legend");
    if (!param || !param.time || !param.seriesData) {
      if (state.lastBar) {
        const b = state.lastBar;
        leg.innerHTML = `${state.selectedSymbol} · ${state.selectedTimeframe} &nbsp; O <b>${fmtPrice(state.selectedSymbol, b.open)}</b> H <b>${fmtPrice(state.selectedSymbol, b.high)}</b> L <b>${fmtPrice(state.selectedSymbol, b.low)}</b> C <b>${fmtPrice(state.selectedSymbol, b.close)}</b>`;
      }
      return;
    }
    const bar = param.seriesData.get(state.candleSeries);
    if (bar) {
      leg.innerHTML = `${state.selectedSymbol} · ${state.selectedTimeframe} &nbsp; O <b>${fmtPrice(state.selectedSymbol, bar.open)}</b> H <b>${fmtPrice(state.selectedSymbol, bar.high)}</b> L <b>${fmtPrice(state.selectedSymbol, bar.low)}</b> C <b>${fmtPrice(state.selectedSymbol, bar.close)}</b>`;
    }
  });

  window.addEventListener("resize", () => {
    if (state.tvChart && container.clientWidth > 0) {
      state.tvChart.applyOptions({ width: container.clientWidth });
    }
  });

  state.tvChart = chart;
}

function updateTvChart(sym, tf, chartData, tick, openTrades) {
  ensureTvChart();
  if (!state.tvChart || !state.candleSeries) return;

  const series = (chartData && chartData.series) || [];
  if (!series.length) return;

  const pf = getPricePrecision(sym);
  state.candleSeries.applyOptions({ priceFormat: { type: "price", precision: pf.precision, minMove: pf.minMove } });

  // Deduplicate & sort strictly ascending timestamps for Lightweight Charts
  const seen = new Set();
  const candles = [];
  const e9 = [];
  const e21 = [];
  const e50 = [];
  for (const c of series) {
    const t = toUnixSec(c.time);
    if (seen.has(t)) continue;
    seen.add(t);
    candles.push({ time: t, open: c.open, high: c.high, low: c.low, close: c.close });
    if (c.ema9 != null) e9.push({ time: t, value: c.ema9 });
    if (c.ema21 != null) e21.push({ time: t, value: c.ema21 });
    if (c.ema50 != null) e50.push({ time: t, value: c.ema50 });
  }
  candles.sort((a, b) => a.time - b.time);

  // Reflect latest live tick on last candle bar
  if (candles.length && tick && tick.mid) {
    const last = candles[candles.length - 1];
    last.close = Number(tick.mid);
    last.high = Math.max(last.high, last.close);
    last.low = Math.min(last.low, last.close);
  }
  state.lastBar = candles[candles.length - 1];

  const key = `${sym}_${tf}`;
  const isNewKey = state.lastSeriesKey !== key;
  if (isNewKey) {
    state.candleSeries.setData(candles);
    state.ema9Series.setData(e9);
    state.ema21Series.setData(e21);
    state.ema50Series.setData(e50);
    state.tvChart.timeScale().scrollToRealTime();
    state.lastSeriesKey = key;
  } else {
    // Live tick update without resetting user's pan/zoom!
    if (state.lastBar) state.candleSeries.update(state.lastBar);
    if (e9.length) state.ema9Series.update(e9[e9.length - 1]);
    if (e21.length) state.ema21Series.update(e21[e21.length - 1]);
    if (e50.length) state.ema50Series.update(e50[e50.length - 1]);
  }

  state.ema9Series.applyOptions({ visible: state.showEmas });
  state.ema21Series.applyOptions({ visible: state.showEmas });
  state.ema50Series.applyOptions({ visible: state.showEmas });

  // Update open trade price lines (Entry / TP / SL)
  state.priceLines.forEach((pl) => {
    try { state.candleSeries.removePriceLine(pl); } catch (_) {}
  });
  state.priceLines = [];
  const symTrades = (openTrades || []).filter((t) => t.symbol === sym);
  for (const t of symTrades) {
    state.priceLines.push(
      state.candleSeries.createPriceLine({ price: t.open_price, color: "#38bdf8", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: `${t.direction} ${t.volume}` }),
      state.candleSeries.createPriceLine({ price: t.tp, color: "#22c55e", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "TP" }),
      state.candleSeries.createPriceLine({ price: t.sl, color: "#ef4444", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "SL" })
    );
  }

  // Update legend
  if (state.lastBar) {
    const b = state.lastBar;
    document.getElementById("tv-legend").innerHTML =
      `${sym} · ${tf} &nbsp; O <b>${fmtPrice(sym, b.open)}</b> H <b>${fmtPrice(sym, b.high)}</b> L <b>${fmtPrice(sym, b.low)}</b> C <b>${fmtPrice(sym, b.close)}</b>`;
  }
}

function renderServerControls(d) {
  const disc = d.mt5_discovery || {};
  const allServers = disc.servers || ["Exness-MT5Trial9", "Exness-MT5Trial", "Exness-MT5Real"];
  const profiles = disc.detected_profiles || [];

  const profEl = document.getElementById("detected-profiles-bar");
  profEl.innerHTML = profiles.length
    ? profiles
        .map(
          (p) =>
            `<button type="button" class="profile-pill" data-login="${p.login || ""}" data-server="${p.server || ""}">
              Detected: #${p.login || "?"} · ${p.server || "Exness"}
            </button>`
        )
        .join("")
    : "";
  profEl.querySelectorAll(".profile-pill").forEach((btn) => {
    btn.onclick = () => {
      if (btn.dataset.login) document.getElementById("mt5-login").value = btn.dataset.login;
      if (btn.dataset.server) {
        state.serverFilter = "ALL";
        renderServerOptions(allServers, btn.dataset.server);
      }
      showBanner(`Pre-filled local Exness MT5 profile #${btn.dataset.login} (${btn.dataset.server}). Enter password and click Connect.`, "info");
    };
  });

  const currentVal = document.getElementById("mt5-server-select").value || (d.settings && d.settings.mt5_server) || allServers[0];
  renderServerOptions(allServers, currentVal);
}

function renderServerOptions(allServers, selectedVal) {
  const sel = document.getElementById("mt5-server-select");
  const filtered = allServers.filter((s) => {
    if (state.serverFilter === "TRIAL") return s.toLowerCase().includes("trial");
    if (state.serverFilter === "REAL") return s.toLowerCase().includes("real");
    return true;
  });
  const list = filtered.length ? filtered : allServers;
  sel.innerHTML = list.map((s) => `<option value="${s}">${s}</option>`).join("");
  if (selectedVal && list.includes(selectedVal)) {
    sel.value = selectedVal;
  }
}

function renderEnabledPairsPicker(d) {
  const allPairs = Object.keys(d.pairs || {});
  const enabled = (d.settings && d.settings.enabled_pairs) || allPairs;
  const grid = document.getElementById("enabled-pairs-grid");
  // Avoid clobbering unsaved toggles while user is editing
  if (grid.dataset.rendered === "1") return;
  grid.innerHTML = allPairs
    .map(
      (sym) =>
        `<label class="pair-check"><input type="checkbox" class="pair-enable-chk" value="${sym}" ${enabled.includes(sym) ? "checked" : ""}/> ${sym}</label>`
    )
    .join("");
  grid.dataset.rendered = "1";
}

function renderTopbar(d) {
  const acc = d.account || {};
  const stats = acc.stats || {};
  const modeEl = document.getElementById("hdr-mode");
  if (acc.mode === "EXNESS_MT5" && acc.connected) {
    modeEl.textContent = `EXNESS ${acc.account_type || "MT5"}`;
    modeEl.className = acc.account_type === "LIVE" ? "badge badge-live" : "badge badge-paper";
  } else {
    modeEl.textContent = "PAPER (LIVE FEED)";
    modeEl.className = "badge badge-paper";
  }

  document.getElementById("hdr-balance").textContent = `$${Number(acc.balance || 0).toFixed(2)}`;
  document.getElementById("hdr-equity").textContent = `$${Number(acc.equity || 0).toFixed(2)}`;
  document.getElementById("hdr-winrate").textContent = `${Number(stats.win_rate || 0).toFixed(1)}% (${stats.wins || 0}W/${stats.losses || 0}L)`;
  document.getElementById("hdr-pf").textContent = Number(stats.profit_factor || 0).toFixed(2);

  const pnl = Number(stats.realized_pnl || 0);
  const pnlEl = document.getElementById("hdr-pnl");
  pnlEl.textContent = `${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}`;
  pnlEl.className = pnl >= 0 ? "pos" : "neg";

  const chip = document.getElementById("mt5-status-chip");
  if (acc.mode === "EXNESS_MT5" && acc.connected) {
    chip.textContent = `MT5 #${acc.login} (${acc.server})`;
  } else if (acc.exness_terminal_detected) {
    chip.textContent = "PAPER (Exness MT5 Ready)";
  } else {
    chip.textContent = "PAPER SANDBOX";
  }

  const s = d.settings || {};
  state.selectedTimeframe = s.timeframe || state.selectedTimeframe;
  document.getElementById("toggle-autopilot").checked = !!s.auto_trade_enabled;
  const execBtn = document.getElementById("btn-run-exec");
  if (s.auto_trade_enabled) {
    execBtn.textContent = "Auto-Trading ON (Stop)";
    execBtn.className = "btn btn-buy";
  } else {
    execBtn.textContent = "Start Auto-Trade";
    execBtn.className = "btn btn-primary";
  }

  if (document.activeElement !== document.getElementById("cfg-lot")) {
    document.getElementById("cfg-lot").value = s.lot_size ?? 0.01;
  }
  document.getElementById("cfg-mode").value = s.execution_mode || "PAPER";
  document.getElementById("cfg-confluence").value = s.min_confluence_score ?? 48;
  if (document.activeElement !== document.getElementById("cfg-maxtrades")) {
    document.getElementById("cfg-maxtrades").value = s.max_concurrent_trades ?? 3;
  }
  document.getElementById("cfg-news-filter").checked = !!s.news_filter_enabled;
  document.getElementById("cfg-live-confirm").checked = !!s.live_confirmed;

  document.querySelectorAll(".lot-chip").forEach((c) => {
    c.classList.toggle("active", Math.abs(parseFloat(c.dataset.lot) - Number(s.lot_size || 0.01)) < 0.001);
  });

  document.querySelectorAll(".tf-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.tf === state.selectedTimeframe);
  });
}

function renderPipelineBar(pipe) {
  if (!pipe) return;
  const ev = (pipe.evaluations || {})[state.selectedSymbol] || {};
  const chart = (state.activeChart && state.activeChart.chart) || ev.chart || {};
  const news = pipe.news || {};
  const items = news.items || [];
  const topPair = pipe.selected_pair || "--";

  document.getElementById("stage-1-meta").textContent =
    `${state.selectedSymbol} (${state.selectedTimeframe}): ${chart.regime || "--"} (${chart.bias || "--"})`;
  document.getElementById("stage-2-meta").textContent = `${items.length} headlines scraped`;
  document.getElementById("stage-3-meta").textContent =
    `${pipe.selected_strategy || "--"} (${pipe.confidence || 0}% score)`;
  document.getElementById("stage-4-meta").textContent = `Top: ${topPair} (${pipe.signal_direction || "HOLD"})`;
  document.getElementById("stage-5-meta").textContent = (pipe.execution && pipe.execution.message) || "Ready";

  ["stage-1", "stage-2", "stage-3", "stage-4"].forEach((id) => {
    document.getElementById(id).className = "stage-card ok";
  });
  document.getElementById("stage-5").className = pipe.executed ? "stage-card ok" : "stage-card active";
}

function renderPairTabs(d) {
  const pairs = Object.keys(d.pairs || {});
  const evals = (d.pipeline && d.pipeline.evaluations) || {};
  const wrap = document.getElementById("pair-tabs");
  if (!pairs.includes(state.selectedSymbol) && pairs.length) {
    state.selectedSymbol = pairs[0];
  }
  wrap.innerHTML = pairs
    .map((sym) => {
      const tick = (evals[sym] && evals[sym].tick) || {};
      const px = fmtPrice(sym, tick.mid);
      return `<button class="pair-tab ${sym === state.selectedSymbol ? "active" : ""}" data-sym="${sym}">
        <span>${sym}</span>
        <span class="tab-px">${px}</span>
      </button>`;
    })
    .join("");
  wrap.querySelectorAll(".pair-tab").forEach((btn) => {
    btn.onclick = async () => {
      state.selectedSymbol = btn.dataset.sym;
      await loadSymbolChart();
      renderAll();
    };
  });
}

function renderChartPanel(d) {
  const pipe = d.pipeline || {};
  const ev = (pipe.evaluations || {})[state.selectedSymbol] || {};
  const useCustom = state.activeChart && state.activeChart.symbol === state.selectedSymbol && state.activeChart.timeframe === state.selectedTimeframe;
  const chart = (useCustom ? state.activeChart.chart : ev.chart) || {};
  const tick = ev.tick || (useCustom ? state.activeChart.tick : {}) || {};
  const ind = chart.indicators || {};
  const strat = ev.strategy || {};

  document.getElementById("chart-symbol").textContent = `${state.selectedSymbol} (${state.selectedTimeframe})`;
  document.getElementById("chart-price").textContent = fmtPrice(state.selectedSymbol, tick.mid ?? ind.close);
  document.getElementById("chart-feed-src").textContent = tick.source || "LIVE";
  document.getElementById("chart-spread").textContent = `Spread: ${tick.spread_pips ?? "--"} pips`;
  document.getElementById("chart-regime").textContent = chart.regime || "--";
  document.getElementById("chart-bias").innerHTML = signalBadge(chart.bias || "HOLD");

  updateTvChart(state.selectedSymbol, state.selectedTimeframe, chart, tick, d.open_trades || []);

  const indItems = [
    ["EMA9", fmtPrice(state.selectedSymbol, ind.ema9)],
    ["EMA21", fmtPrice(state.selectedSymbol, ind.ema21)],
    ["EMA50", fmtPrice(state.selectedSymbol, ind.ema50)],
    ["RSI(14)", ind.rsi ?? "--"],
    ["ADX(14)", ind.adx ?? "--"],
    ["ATR Pips", ind.atr_pips ?? "--"],
    ["Tech Score", `${chart.technical_score ?? 0}%`],
    ["News Bias", strat.news_bias || "NEUTRAL"],
  ];
  document.getElementById("indicators-strip").innerHTML = indItems
    .map(([k, v]) => `<div class="ind-box"><span>${k}</span><strong>${v}</strong></div>`)
    .join("");

  document.getElementById("selected-strat-chip").textContent = strat.best_strategy || "--";
  const cands = strat.candidates || [];
  document.getElementById("strategy-body").innerHTML = cands
    .map(
      (c) => `
      <tr>
        <td><strong>${c.strategy}</strong></td>
        <td>+${c.regime_fit ?? 0}</td>
        <td>${c.news_adjustment >= 0 ? "+" : ""}${c.news_adjustment ?? 0}</td>
        <td>${c.profit_factor}</td>
        <td><strong>${c.composite_score}</strong></td>
      </tr>`
    )
    .join("");

  document.getElementById("signal-reasons").innerHTML = (strat.reasons || [])
    .map((r) => `<li>${r}</li>`)
    .join("");
}

function renderRankings(d) {
  const pipe = d.pipeline || {};
  const rankings = pipe.rankings || [];
  const evals = pipe.evaluations || {};
  document.getElementById("rankings-body").innerHTML = rankings
    .map((r) => {
      const tick = (evals[r.symbol] && evals[r.symbol].tick) || {};
      const px = fmtPrice(r.symbol, tick.mid ?? r.price);
      const src = tick.source || "LIVE";
      return `
      <tr style="cursor:pointer" data-sym="${r.symbol}">
        <td><strong>${r.symbol}</strong></td>
        <td style="font-family:monospace;color:var(--accent)">${px}</td>
        <td><span class="chip">${src}</span></td>
        <td>${signalBadge(r.signal)}</td>
        <td>${r.best_strategy || "--"}</td>
        <td>${r.confidence}%</td>
        <td>${r.news_bias}</td>
        <td>${tick.spread_pips ?? r.spread_pips}</td>
        <td><strong>${r.rank_score}</strong></td>
      </tr>`;
    })
    .join("");

  document.querySelectorAll("#rankings-body tr").forEach((row) => {
    row.onclick = async () => {
      state.selectedSymbol = row.dataset.sym;
      await loadSymbolChart();
      renderAll();
    };
  });
}

function renderNews(d) {
  const news = (d.pipeline && d.pipeline.news) || {};
  const sources = news.sources || [];
  document.getElementById("news-sources").innerHTML = sources
    .map((s) => `<span class="chip">${s.source}: ${s.status} (${s.count})</span>`)
    .join("");

  const items = news.items || [];
  document.getElementById("news-list").innerHTML = items
    .slice(0, 15)
    .map((it) => {
      const sentClass = it.sentiment === "BULLISH" ? "badge-buy" : it.sentiment === "BEARISH" ? "badge-sell" : "badge-hold";
      const titleHtml = it.link
        ? `<a href="${it.link}" target="_blank" rel="noopener noreferrer">${it.title}</a>`
        : `<span>${it.title}</span>`;
      return `
        <div class="news-item">
          <div class="news-item-top">
            <span>${it.source} · ${(it.pairs || []).join(", ")}</span>
            <span class="badge ${sentClass}">${it.sentiment}${it.high_impact ? " · HIGH IMPACT" : ""}</span>
          </div>
          ${titleHtml}
        </div>`;
    })
    .join("");
}

function renderTrades(d) {
  const trades = d.trade_history || [];
  const openCount = (d.open_trades || []).length;
  document.getElementById("open-count-chip").textContent = `${openCount} Open`;

  document.getElementById("trades-body").innerHTML = trades.length
    ? trades
        .map((t) => {
          const pnl = Number(t.profit || 0);
          const pnlCls = pnl >= 0 ? "pos" : "neg";
          const actionBtn =
            t.status === "OPEN"
              ? `<button class="btn btn-sell btn-xs" onclick="closeTrade(${t.ticket})">Close</button>`
              : `<span style="color:var(--muted)">—</span>`;
          return `
            <tr>
              <td>#${t.ticket}</td>
              <td>${t.mode}</td>
              <td><strong>${t.symbol}</strong></td>
              <td>${signalBadge(t.direction)}</td>
              <td>${t.volume}</td>
              <td>${fmtPrice(t.symbol, t.open_price)}</td>
              <td>${fmtPrice(t.symbol, t.sl)}</td>
              <td>${fmtPrice(t.symbol, t.tp)}</td>
              <td>${t.strategy}</td>
              <td>${Number(t.confidence || 0).toFixed(1)}%</td>
              <td class="${pnlCls}">${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}</td>
              <td><span class="chip">${t.status}</span></td>
              <td>${actionBtn}</td>
            </tr>`;
        })
        .join("")
    : `<tr><td colspan="13" style="color:var(--muted);text-align:center;padding:14px;">No trades placed yet. Click "Start Auto-Trade" or Buy/Sell on a pair.</td></tr>`;
}

function renderAll() {
  if (!state.data) return;
  renderTopbar(state.data);
  renderServerControls(state.data);
  renderEnabledPairsPicker(state.data);
  renderPairTabs(state.data);
  renderPipelineBar(state.data.pipeline);
  renderChartPanel(state.data);
  renderRankings(state.data);
  renderNews(state.data);
  renderTrades(state.data);
}

async function loadSymbolChart() {
  try {
    state.activeChart = await api(`/api/chart/${state.selectedSymbol}?tf=${state.selectedTimeframe}`);
  } catch (e) {
    console.warn("Chart reload error", e);
  }
}

async function refreshState() {
  state.data = await api("/api/state");
  if (!state.activeChart || state.activeChart.symbol !== state.selectedSymbol || state.activeChart.timeframe !== state.selectedTimeframe) {
    await loadSymbolChart();
  }
  renderAll();
}

async function streamLiveTick() {
  if (!state.data || !state.tvChart) return;
  try {
    const res = await api(`/api/tick/${state.selectedSymbol}`);
    if (res && res.tick) {
      const pipe = state.data.pipeline || {};
      if (pipe.evaluations && pipe.evaluations[state.selectedSymbol]) {
        pipe.evaluations[state.selectedSymbol].tick = res.tick;
      }
      document.getElementById("chart-price").textContent = fmtPrice(state.selectedSymbol, res.tick.mid);
      document.getElementById("chart-spread").textContent = `Spread: ${res.tick.spread_pips} pips`;
      const useCustom = state.activeChart && state.activeChart.symbol === state.selectedSymbol && state.activeChart.timeframe === state.selectedTimeframe;
      const chart = (useCustom ? state.activeChart.chart : (pipe.evaluations[state.selectedSymbol] || {}).chart) || {};
      updateTvChart(state.selectedSymbol, state.selectedTimeframe, chart, res.tick, state.data.open_trades || []);
    }
  } catch (_) {}
}

window.closeTrade = async (ticket) => {
  const res = await api("/api/close-trade", "POST", { ticket });
  if (res.ok) showBanner(`Closed position #${ticket}.`, "success");
  else showBanner((res.result && res.result.error) || "Failed to close trade.", "error");
  await refreshState();
};

async function saveSettings(extra = {}) {
  const checkedPairs = Array.from(document.querySelectorAll(".pair-enable-chk:checked")).map((c) => c.value);
  const payload = {
    execution_mode: document.getElementById("cfg-mode").value,
    lot_size: parseFloat(document.getElementById("cfg-lot").value),
    min_confluence_score: parseFloat(document.getElementById("cfg-confluence").value),
    max_concurrent_trades: parseInt(document.getElementById("cfg-maxtrades").value, 10),
    enforce_win_rate_gate: false,
    news_filter_enabled: document.getElementById("cfg-news-filter").checked,
    live_confirmed: document.getElementById("cfg-live-confirm").checked,
    ...(checkedPairs.length ? { enabled_pairs: checkedPairs } : {}),
    ...extra,
  };
  const res = await api("/api/settings", "POST", payload);
  if (res.ok) {
    showBanner(`Saved: ${payload.lot_size} lots · Max ${payload.max_concurrent_trades} simultaneous trades · ${(payload.enabled_pairs || []).join(", ")}`, "success");
  }
  await refreshState();
}

// Lot quick-select chips
document.querySelectorAll(".lot-chip").forEach((chip) => {
  chip.onclick = async () => {
    const val = parseFloat(chip.dataset.lot);
    document.getElementById("cfg-lot").value = val;
    await saveSettings({ lot_size: val });
  };
});

// TradingView Chart Toolbar Controls
document.getElementById("btn-pan-back").onclick = () => {
  if (!state.tvChart) return;
  const ts = state.tvChart.timeScale();
  ts.scrollToPosition(ts.scrollPosition() - 25, true);
};
document.getElementById("btn-pan-fwd").onclick = () => {
  if (!state.tvChart) return;
  const ts = state.tvChart.timeScale();
  ts.scrollToPosition(Math.min(6, ts.scrollPosition() + 25), true);
};
document.getElementById("btn-pan-live").onclick = () => {
  if (state.tvChart) state.tvChart.timeScale().scrollToRealTime();
};
document.getElementById("btn-fit-all").onclick = () => {
  if (state.tvChart) state.tvChart.timeScale().fitContent();
};
document.getElementById("btn-zoom-in").onclick = () => {
  if (!state.tvChart) return;
  const ts = state.tvChart.timeScale();
  const lr = ts.getVisibleLogicalRange();
  if (!lr) return;
  const center = (lr.from + lr.to) / 2;
  const span = Math.max(15, (lr.to - lr.from) * 0.7);
  ts.setVisibleLogicalRange({ from: center - span / 2, to: center + span / 2 });
};
document.getElementById("btn-zoom-out").onclick = () => {
  if (!state.tvChart) return;
  const ts = state.tvChart.timeScale();
  const lr = ts.getVisibleLogicalRange();
  if (!lr) return;
  const center = (lr.from + lr.to) / 2;
  const span = Math.min(350, (lr.to - lr.from) * 1.4);
  ts.setVisibleLogicalRange({ from: center - span / 2, to: center + span / 2 });
};
document.getElementById("chk-emas").onchange = (e) => {
  state.showEmas = e.target.checked;
  renderChartPanel(state.data);
};

// Server filter buttons (All / Demo / Live)
document.querySelectorAll(".srv-filter-btn").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".srv-filter-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.serverFilter = btn.dataset.srvType;
    if (state.data) renderServerControls(state.data);
  };
});

document.getElementById("btn-scan").onclick = async () => {
  showBanner("Running Chart Analysis → News Scrape → Strategy & Multi-Pair Selection...");
  const res = await api("/api/run-pipeline", "POST", { execute: false, refresh_news: true });
  showBanner((res.pipeline && res.pipeline.execution && res.pipeline.execution.message) || "Scan complete.", "success");
  await loadSymbolChart();
  await refreshState();
};

document.getElementById("btn-run-exec").onclick = async () => {
  const isOn = !!(state.data && state.data.settings && state.data.settings.auto_trade_enabled);
  if (isOn) {
    await api("/api/auto-trade/stop", "POST", {});
    showBanner("Auto-Trading paused. Existing open trades remain managed.", "info");
  } else {
    // Apply current lot size & pair selection right before starting auto-trade
    await saveSettings({});
    showBanner("Analyzing all enabled pairs & executing simultaneous autonomous trades...");
    const res = await api("/api/auto-trade/start", "POST", { refresh_news: true });
    const exec = (res.pipeline && res.pipeline.execution) || {};
    showBanner(exec.message || "Auto-Trading started.", exec.executed ? "success" : "info");
  }
  await refreshState();
};

document.getElementById("btn-refresh-news").onclick = async () => {
  showBanner("Scraping latest FX & Gold web news...");
  await api("/api/run-pipeline", "POST", { execute: false, refresh_news: true });
  showBanner("News feed updated.", "success");
  await refreshState();
};

document.getElementById("btn-save-settings").onclick = () => saveSettings();
document.getElementById("toggle-autopilot").onchange = async (e) => {
  if (e.target.checked) {
    await saveSettings({});
    showBanner("Auto-Pilot enabled — analyzing and opening multi-pair positions...");
    const res = await api("/api/auto-trade/start", "POST", { refresh_news: false });
    const exec = (res.pipeline && res.pipeline.execution) || {};
    showBanner(exec.message || "Auto-Pilot ON.", exec.executed ? "success" : "info");
    await refreshState();
  } else {
    await api("/api/auto-trade/stop", "POST", {});
    showBanner("Auto-Pilot stopped.", "info");
    await refreshState();
  }
};

document.querySelectorAll(".tf-btn").forEach((btn) => {
  btn.onclick = async () => {
    state.selectedTimeframe = btn.dataset.tf;
    document.querySelectorAll(".tf-btn").forEach((b) => b.classList.toggle("active", b.dataset.tf === state.selectedTimeframe));
    await saveSettings({ timeframe: state.selectedTimeframe });
    await loadSymbolChart();
    renderAll();
  };
});

document.getElementById("btn-manual-buy").onclick = async () => {
  const lot = parseFloat(document.getElementById("cfg-lot").value) || 0.01;
  const res = await api("/api/execute-manual", "POST", { symbol: state.selectedSymbol, direction: "BUY", lot_size: lot });
  showBanner(res.ok ? `Opened BUY ${lot} ${state.selectedSymbol}` : (res.result && res.result.error) || "Order failed", res.ok ? "success" : "error");
  await refreshState();
};

document.getElementById("btn-manual-sell").onclick = async () => {
  const lot = parseFloat(document.getElementById("cfg-lot").value) || 0.01;
  const res = await api("/api/execute-manual", "POST", { symbol: state.selectedSymbol, direction: "SELL", lot_size: lot });
  showBanner(res.ok ? `Opened SELL ${lot} ${state.selectedSymbol}` : (res.result && res.result.error) || "Order failed", res.ok ? "success" : "error");
  await refreshState();
};

document.getElementById("btn-connect-mt5").onclick = async () => {
  const customSrv = document.getElementById("mt5-server-custom").value.trim();
  const selectSrv = document.getElementById("mt5-server-select").value;
  const payload = {
    login: document.getElementById("mt5-login").value.trim() || null,
    password: document.getElementById("mt5-password").value || null,
    server: customSrv || selectSrv || "Exness-MT5Trial9",
  };
  showBanner(`Connecting to Exness MT5 (${payload.server})...`);
  const res = await api("/api/connect-mt5", "POST", payload);
  if (res.ok) {
    showBanner(`Connected to Exness MT5 #${res.mt5.login} (${res.mt5.server} · ${res.mt5.trade_mode})`, "success");
    await api("/api/run-pipeline", "POST", { execute: false, refresh_news: false });
    await loadSymbolChart();
  } else {
    showBanner((res.mt5 && res.mt5.error) || "Could not connect to Exness MT5.", "error");
  }
  await refreshState();
};

document.getElementById("btn-disconnect-mt5").onclick = async () => {
  await api("/api/disconnect-mt5", "POST", {});
  showBanner("Disconnected MT5. Switched back to Paper Sandbox with Live Market Feed.", "info");
  await refreshState();
};

async function pollLoop() {
  try {
    await refreshState();
  } catch (e) {
    console.warn("Poll error", e);
  } finally {
    setTimeout(pollLoop, 5000);
  }
}

async function tickLoop() {
  try {
    await streamLiveTick();
  } finally {
    setTimeout(tickLoop, 2000);
  }
}

pollLoop();
setTimeout(tickLoop, 2000);
