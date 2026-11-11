// Painel do agente (somente leitura). Atualiza o estado a cada 2 s e o gráfico a cada 20 s.
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = (x, d = 2) => (x === null || x === undefined || Number.isNaN(x) ? "-" : Number(x).toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d }));
const pct = (x, d = 2, sign = false) => (x === null || x === undefined ? "-" : (sign && x > 0 ? "+" : "") + nf(x, d) + "%");
const brl = (x) => (x === null || x === undefined ? "-" : (x < 0 ? "−" : "") + "R$ " + nf(Math.abs(x)));
const ago = (s) => (s === null || s === undefined ? "-" : s < 60 ? `${nf(s, 0)} s` : s < 3600 ? `${nf(s / 60, 0)} min` : `${nf(s / 3600, 1)} h`);
const TZ = "America/Sao_Paulo";
const REGIMES = { tendencia_alta: "tendência de alta", tendencia_baixa: "tendência de baixa", lateral: "lateral", alta_volatilidade: "alta volatilidade", transicao: "transição", indefinido: "indefinido" };
const when = (ms) => new Date(ms).toLocaleString("pt-BR", { timeZone: TZ, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

let state = null;
let tf = "15m";
let lastAlertTime = 0;

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", "X-Painel-Token": window.PAINEL_TOKEN, ...(opts.headers || {}) } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
  return body;
}

function setList(el, items) {
  el.innerHTML = (items || []).length ? items.map((x) => `<li>${esc(x)}</li>`).join("") : '<li class="muted">—</li>';
}

// ------------------------------------------------------------------ estado
function render(s) {
  state = s;
  $("clock").textContent = new Date(s.agora_ms).toLocaleTimeString("pt-BR", { timeZone: TZ }) + " (Brasília)";
  $("fatal").hidden = !s.fatal; $("fatal").textContent = s.fatal ? "RECUSADO: " + s.fatal : "";
  $("error").hidden = !s.erro; $("error").textContent = s.erro ? "Falha de dados: " + s.erro + " — sinal em ESPERAR." : "";

  const ws = s.ws || {};
  const pd = $("pill-data");
  pd.textContent = ws.modo || "—";
  pd.className = "pill " + (ws.modo && ws.modo.startsWith("WebSocket") ? "ok" : "warn");
  $("ws").textContent = `WebSocket ${ws.conectado ? "conectado" : "desconectado"} · última mensagem há ${ago(ws.ultima_msg_s)} · reconexões ${ws.reconexoes ?? 0}`;

  const d = s.decisao;
  if (!d) return;
  const live = s.precos && s.precos[s.par];
  const price = live ? (live.bid + live.ask) / 2 : d.preco;
  $("pair").textContent = s.par;
  $("price").textContent = nf(price);
  const act = $("action");
  act.textContent = d.acao;
  act.className = "action " + ({ COMPRAR: "buy", VENDER: "sell", ESPERAR: "wait" }[d.acao] || "wait");
  $("conf-fill").style.width = Math.max(0, Math.min(100, d.confianca)) + "%";
  $("conf-text").textContent = `confiança ${nf(d.confianca, 0)}% · regime ${REGIMES[d.regime] || d.regime || "-"}`;
  $("updated").textContent = `análise #${d.sinal_id} atualizada há ${ago(d.idade_s)} · preço ao vivo há ${ago(live && live.idade_s)}`;
  $("position").hidden = !d.posicao; $("position").textContent = d.posicao || "";
  $("hour-note").textContent = s.horario ? "Horário: " + s.horario : "";

  $("blocks-wrap").hidden = !(d.travas || []).length;
  setList($("blocks"), d.travas);
  setList($("pros"), d.a_favor);
  setList($("cons"), d.contra);
  $("technical-note").hidden = !(d.acao === "ESPERAR" && (d.travas || []).length && (d.parcelas || []).length);
  setList($("invalid"), d.invalidacao);

  const lv = d.niveis;
  $("levels").innerHTML = lv
    ? `<tbody>
        <tr><td>Entrada</td><td>${nf(lv.entrada)}</td></tr>
        <tr><td>Stop-loss (obrigatório)</td><td class="stop">${nf(lv.stop)} (${pct((lv.stop / lv.entrada - 1) * 100, 2, true)})</td></tr>
        <tr><td>Alvo 1 <span class="muted small">${esc(lv.origem_alvo1)}</span></td><td>${nf(lv.alvo1)} (${pct((lv.alvo1 / lv.entrada - 1) * 100, 2, true)})</td></tr>
        <tr><td>Alvo 2</td><td>${nf(lv.alvo2)}</td></tr>
        <tr><td>Risco/retorno (já com taxas)</td><td>${nf(lv.rr)}</td></tr>
        <tr><td>Tamanho sugerido</td><td>${brl(lv.tamanho_brl)}</td></tr>
        <tr><td>Se o stop for atingido, perde ≈</td><td class="stop">${brl(lv.perda_no_stop)}</td></tr>
        ${d.acao !== "COMPRAR" ? '<tr><td colspan="2" class="muted small">Só referência: a decisão não é COMPRAR.</td></tr>' : ""}
      </tbody>`
    : '<tbody><tr><td class="muted">sem níveis nesta leitura</td></tr></tbody>';
  $("oco").hidden = d.acao !== "COMPRAR";

  $("parts").querySelector("tbody").innerHTML = (d.parcelas || []).length
    ? d.parcelas.map((p) => `<tr><td>${esc(p.name)}</td><td class="num">${nf(p.value)}</td><td class="small">${esc(p.explanation)}</td></tr>`).join("") +
      `<tr><td><b>= confiança</b></td><td class="num"><b>${nf(d.confianca, 0)}%</b></td><td></td></tr>`
    : '<tr><td colspan="3" class="muted">sem cálculo nesta leitura (travas antes dos sinais)</td></tr>';

  renderAccount(s.conta);
  renderHealth(s.saude);
  renderCosts(s.custos);
  renderSignals(s.sinais || []);
  renderBuys(s.compras || [], s.sinais_compra || []);
  renderPaper(s.paper || []);
  renderAlerts(s.alertas || []);
}

function renderAccount(c) {
  const el = $("account");
  $("session-card").hidden = !(c && !c.sessao);
  if (!c) { el.innerHTML = '<p class="muted">sem dados da conta</p>'; return; }
  const parts = [`<p class="small muted">Conta: ${esc(c.modo)} (${c.verificado ? "verificada pela Binance" : "NÃO verificada"})</p>`];
  if (c.sessao) {
    parts.push(`<table class="kv"><tbody>
      <tr><td>Capital de hoje</td><td>${brl(c.sessao.capital_brl)}</td></tr>
      <tr><td>Perda máxima de hoje</td><td>${brl(c.sessao.max_loss_brl)}</td></tr>
      <tr><td>Par · saldo em</td><td>${esc(c.sessao.par_operacao)} · ${esc(c.sessao.saldo_em)}</td></tr></tbody></table>`);
  }
  if (c.limites) {
    const l = c.limites;
    const used = Math.min(100, (l.loss_used_brl / l.max_loss_brl) * 100);
    parts.push(`<table class="kv"><tbody>
      <tr><td>Resultado do dia</td><td class="${l.pnl_brl < 0 ? "bad" : l.pnl_brl > 0 ? "ok" : ""}">${brl(l.pnl_brl)} (${pct(l.pnl_pct, 2, true)})</td></tr>
      <tr><td>Resta da perda máxima</td><td>${brl(l.remaining_brl)}</td></tr>
      <tr><td>Risco por operação</td><td>${brl(l.risk_per_trade_brl)}</td></tr></tbody></table>
      <div class="bar" title="perda usada"><div style="width:${used}%"></div></div>
      <p class="small muted">perda usada ${nf(used, 0)}%${l.blocked ? ' · <b class="bad">PERDA MÁXIMA ATINGIDA: só ESPERAR</b>' : ""}</p>`);
  }
  if (c.dia && c.dia.posicao > 0) parts.push(`<p class="small">Posição da operação: ${nf(c.dia.posicao, 6)} ETH a custo médio ${nf(c.dia.custo_medio)} ${esc(c.moeda)}</p>`);
  (c.travas || []).forEach((t) => parts.push(`<p class="small warnc">• ${esc(t)}</p>`));
  (c.avisos || []).forEach((t) => parts.push(`<p class="small muted">aviso: ${esc(t)}</p>`));
  el.innerHTML = parts.join("");
}

function renderHealth(h) {
  if (!h) return;
  const pill = $("pill-health");
  pill.textContent = h.ok ? "dados em ordem" : "PROBLEMA nos dados";
  pill.className = "pill " + (h.ok ? "ok" : "bad");
  $("health").querySelector("tbody").innerHTML = h.verificacoes
    .map((c) => `<tr><td>${esc(c.name)}</td><td class="${c.ok ? "ok" : c.critical ? "bad" : "warnc"}">${c.ok ? "OK" : c.critical ? "PROBLEMA" : "aviso"}</td><td class="small">${esc(c.detail)}</td></tr>`)
    .join("");
  $("latency").textContent = `latência da análise ${nf(h.latencia_ms, 0)} ms`;
}

function renderCosts(c) {
  if (!c) return;
  const over = c.spread_pct !== null && c.spread_pct > c.spread_max_pct;
  $("costs").innerHTML = `<table class="kv"><tbody>
    <tr><td>Spread agora (${esc(state.par)})</td><td class="${over ? "bad" : ""}">${pct(c.spread_pct, 4)} <span class="muted small">limite ${pct(c.spread_max_pct, 2)}</span></td></tr>
    <tr><td>Custo de ida e volta (ponto de equilíbrio)</td><td>${pct(c.custo_ida_volta_pct, 3)}</td></tr></tbody></table>`;
}

function renderSignals(rows) {
  $("signals").querySelector("tbody").innerHTML = rows.length
    ? rows.map((r) => `<tr><td>${r.id}</td><td>${when(r.time_ms)}</td><td><span class="tag ${esc(r.action)}">${esc(r.action)}</span></td><td class="num">${nf(r.confidence, 0)}%</td><td class="num">${nf(r.price)}</td><td class="num">${nf(r.stop)}</td><td class="num">${nf(r.target1)}</td></tr>`).join("")
    : '<tr><td colspan="7" class="muted">nenhum sinal ainda</td></tr>';
}

function renderBuys(rows, buySignals) {
  const opts = '<option value="nenhum">nenhum</option>' + buySignals.map((s) => `<option value="${s.id}">#${s.id} · ${when(s.time_ms)} · ${nf(s.price)}</option>`).join("");
  const tb = $("buys").querySelector("tbody");
  if (tb.contains(document.activeElement)) return; // não atrapalha quem está escolhendo
  tb.innerHTML = rows.length
    ? rows.map((r) => `<tr data-par="${esc(r.symbol)}" data-op="${r.trade_id}">
        <td>${when(r.time_ms)}</td><td class="num">${nf(r.price)}</td><td class="num">${nf(r.qty, 6)}</td>
        <td><select class="sig">${opts.replace(`value="${r.signal_id}"`, `value="${r.signal_id}" selected`)}</select></td>
        <td class="small">${esc(r.association)}</td><td><button class="mini assoc">${r.association === "automatica" ? "Confirmar" : "Salvar"}</button></td></tr>`).join("")
    : '<tr><td colspan="6" class="muted">nenhuma compra lida da Binance desde o início do livro da operação</td></tr>';
  tb.querySelectorAll("button.assoc").forEach((b) =>
    b.addEventListener("click", async () => {
      const tr = b.closest("tr");
      try {
        await api("/api/associar", { method: "POST", body: JSON.stringify({ par: tr.dataset.par, operacao: tr.dataset.op, sinal: tr.querySelector("select").value }) });
        b.textContent = "salvo ✓";
      } catch (e) { b.textContent = "erro"; alert(e.message); }
    })
  );
}

function renderPaper(rows) {
  $("paper").querySelector("tbody").innerHTML = rows.length
    ? rows.map((r) => `<tr><td>${esc(r.variant)}</td><td class="num">${nf(r.equity)}</td><td class="num">${r.fechadas}</td><td class="num">${r.abertas}</td><td>${when(r.started_ms)}</td></tr>`).join("")
    : '<tr><td colspan="5" class="muted">paper trading ainda não iniciado</td></tr>';
}

function renderAlerts(list) {
  $("alerts").innerHTML = list.length ? list.map((a) => `<li class="${a.urgent ? "bad" : ""}"><b>${when(a.time_ms)}</b> ${esc(a.title)} <span class="muted">— ${esc(a.body)}</span></li>`).join("") : '<li class="muted">nenhum alerta</li>';
  const newest = list[0];
  if (newest && newest.time_ms > lastAlertTime) {
    if (lastAlertTime) {
      const b = $("alert-banner");
      b.hidden = false;
      b.textContent = `${newest.title} — ${newest.body}`;
      if ("Notification" in window && Notification.permission === "granted") new Notification(newest.title, { body: newest.body });
    }
    lastAlertTime = newest.time_ms;
  }
}

// ------------------------------------------------------------------ gráfico
let chart = null, series = {};
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function initChart() {
  if (!window.LightweightCharts) { $("chart").innerHTML = '<p class="muted">Gráfico indisponível (sem internet para carregar a biblioteca). O resto do painel funciona.</p>'; return; }
  chart = LightweightCharts.createChart($("chart"), {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: css("--muted") },
    grid: { vertLines: { color: css("--line") }, horzLines: { color: css("--line") } },
    timeScale: { timeVisible: true, secondsVisible: false },
    localization: { locale: "pt-BR", priceFormatter: (p) => nf(p) },
  });
  series.candles = chart.addCandlestickSeries({ upColor: css("--buy"), downColor: css("--sell"), wickUpColor: css("--buy"), wickDownColor: css("--sell"), borderVisible: false });
  const line = (color, width = 1, style = 0) => chart.addLineSeries({ color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false });
  series.ema_20 = line(css("--ema20")); series.ema_50 = line(css("--ema50")); series.ema_200 = line(css("--ema200"), 2);
  series.bb_upper = line(css("--bb"), 1, 2); series.bb_mid = line(css("--bb"), 1, 1); series.bb_lower = line(css("--bb"), 1, 2);
  series.vwap = line(css("--vwap"), 2);
}
let priceLines = [];
async function loadCandles() {
  if (!chart) return;
  try {
    const c = await api(`/api/candles?tf=${tf}`);
    $("chart-sym").textContent = c.simbolo ? `· ${c.simbolo} ${tf}` : "";
    series.candles.setData(c.candles.map((r) => ({ time: r.t, open: r.open, high: r.high, low: r.low, close: r.close })));
    for (const k of ["ema_20", "ema_50", "ema_200", "bb_upper", "bb_mid", "bb_lower", "vwap"]) series[k].setData(c.candles.filter((r) => r[k] !== null).map((r) => ({ time: r.t, value: r[k] })));
    priceLines.forEach((pl) => series.candles.removePriceLine(pl));
    priceLines = [];
    const add = (price, title, color, style = 2) => price && priceLines.push(series.candles.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title }));
    const n = c.niveis || {};
    add(n.entrada, "entrada", css("--accent")); add(n.stop, "stop", css("--sell"), 0); add(n.alvo1, "alvo 1", css("--buy")); add(n.alvo2, "alvo 2", css("--buy"));
  } catch (e) { /* tenta de novo no próximo ciclo */ }
}

// ------------------------------------------------------------- mapa de calor
async function loadHeat() {
  try {
    const h = await api("/api/sazonalidade");
    $("heat-sym").textContent = h.simbolo ? "· " + h.simbolo : "";
    if (!h.disponivel) { $("heat").innerHTML = '<tr><td class="muted">rode: cryptoagent sazonalidade analisar</td></tr>'; return; }
    const grid = h.heatmap.pct_above_cost, days = h.heatmap.weekdays;
    const now = new Date(new Date().toLocaleString("en-US", { timeZone: TZ }));
    const nowDay = (now.getDay() + 6) % 7, nowHour = now.getHours();
    let html = "<tr><th></th>" + days.map((d) => `<th>${esc(d)}</th>`).join("") + "</tr>";
    for (let hr = 0; hr < 24; hr++) {
      html += `<tr><th>${String(hr).padStart(2, "0")}h</th>` + days.map((_, d) => {
        const v = grid[d][hr];
        const light = v === null ? 90 : 94 - 0.52 * Math.max(0, Math.min(100, v));
        return `<td class="${d === nowDay && hr === nowHour ? "now" : ""}" style="background:hsl(${css("--heat")} ${light}%);color:${light < 55 ? "#fff" : "#111"}">${v === null ? "–" : nf(v, 0)}</td>`;
      }).join("") + "</tr>";
    }
    $("heat").innerHTML = html;
  } catch (e) { /* ignora */ }
}

// ---------------------------------------------------------------- eventos
$("tf-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  tf = b.dataset.tf;
  document.querySelectorAll("#tf-tabs button").forEach((x) => x.classList.toggle("on", x === b));
  loadCandles();
});
$("session-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const msg = $("session-msg");
  try {
    await api("/api/sessao", { method: "POST", body: JSON.stringify(Object.fromEntries(f.entries())) });
    msg.textContent = "Sessão salva. Reanalisando…"; msg.className = "small ok";
  } catch (err) { msg.textContent = err.message; msg.className = "small bad"; }
});
$("notif-btn").addEventListener("click", async () => {
  if (!("Notification" in window)) return alert("Este navegador não suporta notificações.");
  const p = await Notification.requestPermission();
  $("notif-btn").textContent = p === "granted" ? "Notificações ativadas ✓" : "Notificações bloqueadas";
});

async function tick() {
  try { render(await api("/api/estado")); }
  catch (e) { $("pill-data").textContent = "painel sem conexão com o agente"; $("pill-data").className = "pill bad"; }
}
initChart();
tick(); loadCandles(); loadHeat();
setInterval(tick, 2000);
setInterval(loadCandles, 20000);
setInterval(loadHeat, 300000);
