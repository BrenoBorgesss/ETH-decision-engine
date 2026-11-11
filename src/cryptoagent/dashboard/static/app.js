// Painel do agente (somente leitura). Estado a cada 2 s, gráfico a cada 20 s, abas Simulação e Operações reais sob demanda.
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isNum = (x) => x !== null && x !== undefined && !Number.isNaN(Number(x));
const nf = (x, d = 2) => (!isNum(x) ? "-" : Number(x).toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d }));
const pct = (x, d = 2, sign = false) => (!isNum(x) ? "-" : (sign && x > 0 ? "+" : "") + nf(x, d) + "%");
const brl = (x, sign = false) => (!isNum(x) ? "-" : (x < 0 ? "−" : sign && x > 0 ? "+" : "") + "R$ " + nf(Math.abs(x)));
const ago = (s) => (!isNum(s) ? "-" : s < 60 ? `${nf(s, 0)} s` : s < 3600 ? `${nf(s / 60, 0)} min` : `${nf(s / 3600, 1)} h`);
const TZ = "America/Sao_Paulo";
const REGIMES = { tendencia_alta: "tendência de alta", tendencia_baixa: "tendência de baixa", lateral: "lateral", alta_volatilidade: "alta volatilidade", transicao: "transição", indefinido: "indefinido" };
const when = (ms) => (isNum(ms) ? new Date(ms).toLocaleString("pt-BR", { timeZone: TZ, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "-");
const params = new URLSearchParams(location.search);

let state = null;
let tf = "15m";
let lastAlertTime = 0;
let failures = 0;
let tab = "agora";

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", "X-Painel-Token": window.PAINEL_TOKEN, ...(opts.headers || {}) } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `o painel respondeu com erro (${r.status})`);
  return body;
}
const setList = (el, items) => { el.innerHTML = (items || []).length ? items.map((x) => `<li>${esc(x)}</li>`).join("") : '<li class="muted">—</li>'; };
const badge = (sim) => (sim ? '<span class="badge sim">SIMULADO</span>' : '<span class="badge real">REAL</span>');

// ------------------------------------------------------------------ tema
const THEMES = ["", "light", "dark"];
const THEME_PT = { "": "automático", light: "claro", dark: "escuro" };
function currentTheme() { return document.documentElement.dataset.theme || ""; }
function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  try { localStorage.setItem("tema", t); } catch (e) { /* navegador sem armazenamento: só vale nesta aba */ }
  $("theme-btn").textContent = "Tema: " + THEME_PT[t];
  rebuildChart();
}
$("theme-btn").addEventListener("click", () => applyTheme(THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length]));
$("theme-btn").textContent = "Tema: " + THEME_PT[currentTheme()];

// ------------------------------------------------------------------ abas
function showTab(name) {
  tab = name;
  document.querySelectorAll(".tabbar button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  for (const t of ["agora", "simulacao", "reais"]) $("tab-" + t).hidden = t !== name;
  if (name === "simulacao") loadSimulation();
  if (name === "reais") loadJournal();
  if (name === "agora" && chart) chart.timeScale().fitContent();
}
document.querySelector(".tabbar").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) showTab(b.dataset.tab); });

// ------------------------------------------------------------------ estado (aba Agora)
function render(s) {
  state = s;
  const demo = s.modo === "demo";
  const mb = $("mode-badge");
  mb.className = "badge " + (demo ? "sim" : "real");
  mb.textContent = demo ? "DEMONSTRAÇÃO · SIMULADO" : "REAL · somente leitura";
  $("clock").textContent = new Date(s.agora_ms).toLocaleString("pt-BR", { timeZone: TZ, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) + (demo ? " (relógio simulado)" : " (Brasília)");
  $("fatal").hidden = !s.fatal; $("fatal").textContent = s.fatal ? "RECUSADO: " + s.fatal : "";
  $("error").hidden = !s.erro; $("error").textContent = s.erro ? "Falha de dados: " + s.erro + " — por segurança, o sinal fica em ESPERAR." : "";

  const ws = s.ws || {};
  const pd = $("pill-data");
  pd.textContent = demo ? "replay do histórico (sem internet)" : ws.modo || "—";
  pd.className = "pill " + (demo || (ws.modo && ws.modo.startsWith("WebSocket")) ? "ok" : "warn");
  $("ws").textContent = demo ? "Demonstração: os dados vêm do histórico de exemplo, num relógio acelerado." : `WebSocket ${ws.conectado ? "conectado" : "desconectado"} · última mensagem há ${ago(ws.ultima_msg_s)} · reconexões ${ws.reconexoes ?? 0}`;
  $("buys-card").hidden = demo;
  $("signals-badge").outerHTML = `<span id="signals-badge">${badge(demo)}</span>`;

  const d = s.decisao;
  $("loading").hidden = !!d;
  $("kpis").hidden = !d;
  if (!d) return;
  const live = s.precos && s.precos[s.par];
  const price = live ? (live.bid + live.ask) / 2 : d.preco;
  const fx = s.precos && s.precos.USDTBRL ? (s.precos.USDTBRL.bid + s.precos.USDTBRL.ask) / 2 : null;
  const inBrl = s.par && s.par.endsWith("BRL");
  $("pair").textContent = s.par;
  $("price").textContent = inBrl ? brl(price) : nf(price) + " USDT";
  $("price-brl").textContent = inBrl ? "preço médio entre compra e venda" : fx ? `≈ ${brl(price * fx)} pelo câmbio USDTBRL` : "sem câmbio USDTBRL para converter";
  const act = $("action");
  act.textContent = d.acao;
  act.className = "action " + ({ COMPRAR: "buy", VENDER: "sell", ESPERAR: "wait" }[d.acao] || "wait");
  $("signal-badge").className = "badge " + (demo ? "sim" : "real"); $("signal-badge").textContent = demo ? "SIMULADO" : "REAL";
  $("updated").textContent = `análise #${d.sinal_id ?? "-"} há ${ago(d.idade_s)}`;
  $("conf-value").textContent = nf(d.confianca, 0) + "%";
  $("conf-fill").style.width = Math.max(0, Math.min(100, d.confianca)) + "%";
  $("conf-text").textContent = `regime ${REGIMES[d.regime] || d.regime || "-"} · pontuação, não probabilidade`;
  renderPosition(s, demo);

  $("blocks-wrap").hidden = !(d.travas || []).length;
  setList($("blocks"), d.travas);
  setList($("pros"), d.a_favor);
  setList($("cons"), d.contra);
  $("technical-note").hidden = !(d.acao === "ESPERAR" && (d.travas || []).length && (d.parcelas || []).length);
  setList($("invalid"), d.invalidacao);
  renderLevels(d, demo);

  $("parts").querySelector("tbody").innerHTML = (d.parcelas || []).length
    ? d.parcelas.map((p) => `<tr><td>${esc(p.name)}</td><td class="num">${nf(p.value)}</td><td class="small">${esc(p.explanation)}</td></tr>`).join("") +
      `<tr><td><b>= confiança</b></td><td class="num"><b>${nf(d.confianca, 0)}%</b></td><td></td></tr>`
    : '<tr><td colspan="3" class="muted">sem cálculo nesta leitura (travas antes dos sinais)</td></tr>';

  if (demo) renderWallet(s.carteira_virtual); else renderAccount(s.conta);
  renderPanorama(s.panorama);
  renderHealth(s.saude);
  renderCosts(s.custos);
  renderSignals(s.sinais || []);
  if (!demo) renderBuys(s.compras || [], s.sinais_compra || []);
  renderAlerts(s.alertas || []);
}

function renderPosition(s, demo) {
  const pb = $("pos-badge"); pb.className = "badge " + (demo ? "sim" : "real"); pb.textContent = demo ? "SIMULADO" : "REAL";
  let qty = null, sub = "";
  if (demo && s.carteira_virtual && s.carteira_virtual.posicao) {
    const p = s.carteira_virtual.posicao; qty = p.qtd; sub = `entrada ${brl(p.entrada)} · stop ${brl(p.stop)} · alvo ${brl(p.alvo1)}`;
  } else if (!demo && s.conta && s.conta.dia && s.conta.dia.posicao > 0) {
    qty = s.conta.dia.posicao; sub = `custo médio ${nf(s.conta.dia.custo_medio)} ${esc(s.conta.moeda)}`;
  }
  $("pos-value").textContent = qty ? nf(qty, 6) + " ETH" : "sem posição";
  $("pos-sub").textContent = qty ? sub : "nada comprado pela estratégia agora";
}

function renderLevels(d, demo) {
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
  $("oco").hidden = d.acao !== "COMPRAR" || demo;
}

function renderWallet(w) {
  $("account-card").className = "card is-sim";
  $("account-title").innerHTML = 'Carteira virtual e limites do dia <span class="badge sim">SIMULADO</span>';
  $("session-card").hidden = true;
  if (!w) { $("account").innerHTML = '<p class="muted">aguardando…</p>'; return; }
  const used = w.perda_max_dia ? Math.min(100, Math.max(0, -w.resultado_dia_brl) / w.perda_max_dia * 100) : 0;
  $("account").innerHTML = `<table class="kv"><tbody>
    <tr><td>Capital virtual inicial</td><td>${brl(w.capital_inicial)}</td></tr>
    <tr><td>Patrimônio virtual</td><td>${brl(w.patrimonio)}</td></tr>
    <tr><td>Resultado total</td><td class="${w.resultado_brl < 0 ? "bad" : w.resultado_brl > 0 ? "ok" : ""}">${brl(w.resultado_brl, true)}</td></tr>
    <tr><td>Resultado do dia</td><td class="${w.resultado_dia_brl < 0 ? "bad" : w.resultado_dia_brl > 0 ? "ok" : ""}">${brl(w.resultado_dia_brl, true)}</td></tr>
    <tr><td>Perda máxima do dia</td><td>${brl(w.perda_max_dia)}</td></tr></tbody></table>
    <div class="bar" title="perda usada"><div style="width:${used}%"></div></div>
    <p class="small muted">perda do dia usada ${nf(used, 0)}%${w.fim_do_periodo ? " · <b>fim do período da demonstração</b>" : ""}</p>`;
}

function renderAccount(c) {
  $("account-card").className = "card is-real";
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
      <tr><td>Resultado do dia</td><td class="${l.pnl_brl < 0 ? "bad" : l.pnl_brl > 0 ? "ok" : ""}">${brl(l.pnl_brl, true)} (${pct(l.pnl_pct, 2, true)})</td></tr>
      <tr><td>Resta da perda máxima</td><td>${brl(l.remaining_brl)}</td></tr>
      <tr><td>Risco por operação</td><td>${brl(l.risk_per_trade_brl)}</td></tr></tbody></table>
      <div class="bar" title="perda usada"><div style="width:${used}%"></div></div>
      <p class="small muted">perda usada ${nf(used, 0)}%${l.blocked ? ' · <b class="bad">PERDA MÁXIMA ATINGIDA: só ESPERAR</b>' : ""}</p>`);
  }
  (c.travas || []).forEach((t) => parts.push(`<p class="small warnc">• ${esc(t)}</p>`));
  (c.avisos || []).forEach((t) => parts.push(`<p class="small muted">aviso: ${esc(t)}</p>`));
  el.innerHTML = parts.join("");
}

function renderPanorama(p) {
  $("panorama-card").hidden = !p;
  if (!p) return;
  const [lo, hi] = p.acerto_ic95 || [null, null];
  const [mlo, mhi] = p.media_ic95_pct || [null, null];
  $("panorama").innerHTML = `<p class="small muted">${esc(p.cenario)} · ${p.casos} casos no histórico${p.fator < 1 ? ` · cenário pouco visto: confiança × ${nf(p.fator, 2)}` : ""}</p>
    <div class="wallet-kpis">
      <div class="mini-kpi">acerto<b>${pct(p.acerto * 100, 0)}</b><span class="small muted">IC 95%: ${pct(lo * 100, 0)} a ${pct(hi * 100, 0)}</span></div>
      <div class="mini-kpi">ganho médio<b class="ok">${pct(p.ganho_medio_pct, 2, true)}</b></div>
      <div class="mini-kpi">perda média<b class="bad">${pct(p.perda_media_pct, 2, true)}</b></div>
      <div class="mini-kpi">média por operação<b class="${p.media_pct < 0 ? "bad" : "ok"}">${pct(p.media_pct, 3, true)}</b><span class="small muted">IC: ${pct(mlo, 3, true)} a ${pct(mhi, 3, true)}</span></div>
    </div><p class="small muted">Descrição do passado (com custos), não previsão. Com muitos cenários comparados, um resultado bom pode ser sorte.</p>`;
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
        <td class="small">${esc(r.association)}</td><td><button class="mini assoc" type="button">${r.association === "automatica" ? "Confirmar" : "Salvar"}</button></td></tr>`).join("")
    : '<tr><td colspan="6" class="muted">nenhuma compra lida da Binance desde o início do livro da operação</td></tr>';
  tb.querySelectorAll("button.assoc").forEach((b) =>
    b.addEventListener("click", async () => {
      const tr = b.closest("tr");
      try {
        await api("/api/associar", { method: "POST", body: JSON.stringify({ par: tr.dataset.par, operacao: tr.dataset.op, sinal: tr.querySelector("select").value }) });
        b.textContent = "salvo ✓";
      } catch (e) { b.textContent = "não salvou"; b.title = e.message; }
    })
  );
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

// ------------------------------------------------------------------ gráficos SVG (simulação)
function lineSvg(points, { height = 230, baseline = null, color = "var(--sim)" } = {}) {
  if (points.length < 2) return '<p class="muted small">a curva aparece depois da primeira operação fechada</p>';
  const w = 1000, h = height, pad = 80;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys, baseline ?? Infinity), y1 = Math.max(...ys, baseline ?? -Infinity);
  if (y1 - y0 < 1e-9) { y0 -= 1; y1 += 1; }
  const sx = (x) => pad + ((x - x0) / Math.max(1, x1 - x0)) * (w - 2 * pad);
  const sy = (y) => h - pad + 6 - ((y - y0) / (y1 - y0)) * (h - 2 * pad);
  const path = points.map((p, i) => `${i ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(" ");
  const base = baseline === null ? "" : `<line x1="${pad}" x2="${w - pad}" y1="${sy(baseline)}" y2="${sy(baseline)}" stroke="currentColor" stroke-opacity=".3" stroke-dasharray="4 4"/>`;
  return `<svg class="svgchart" viewBox="0 0 ${w} ${h}" role="img" aria-label="curva de capital">${base}
    <path d="${path}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linejoin="round"/>
    ${points.map((p) => `<circle cx="${sx(p[0]).toFixed(1)}" cy="${sy(p[1]).toFixed(1)}" r="2.5" fill="${color}"><title>${when(p[0])}: ${brl(p[1])}</title></circle>`).join("")}
    <text x="4" y="${sy(y1) + 4}" font-size="11" fill="currentColor" opacity=".65">${brl(y1)}</text>
    <text x="4" y="${sy(y0) + 4}" font-size="11" fill="currentColor" opacity=".65">${brl(y0)}</text>
    <text x="${pad}" y="${h - 4}" font-size="11" fill="currentColor" opacity=".65">${when(x0)}</text>
    <text x="${w - pad}" y="${h - 4}" font-size="11" fill="currentColor" opacity=".65" text-anchor="end">${when(x1)}</text></svg>`;
}

function wilson(k, n) {
  if (!n) return [NaN, NaN];
  const z = 1.96, p = k / n, den = 1 + (z * z) / n;
  const c = (p + (z * z) / (2 * n)) / den, half = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / den;
  return [Math.max(0, c - half), Math.min(1, c + half)];
}

function calibSvg(pts, title) {
  if (!pts.length) return `<figure><p class="muted small">sem dados de ${esc(title)} ainda</p></figure>`;
  const w = 300, h = 260, pad = 36;
  const sx = (x) => pad + x * (w - 2 * pad), sy = (y) => h - pad - y * (h - 2 * pad);
  let s = `<svg class="svgchart" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(title)}">`;
  for (const v of [0, 0.5, 1]) {
    s += `<line x1="${sx(0)}" x2="${sx(1)}" y1="${sy(v)}" y2="${sy(v)}" stroke="currentColor" stroke-opacity=".12"/>`;
    s += `<text x="${pad - 6}" y="${sy(v) + 4}" font-size="10" text-anchor="end" fill="currentColor" opacity=".6">${v * 100}%</text>`;
    s += `<text x="${sx(v)}" y="${h - pad + 14}" font-size="10" text-anchor="middle" fill="currentColor" opacity=".6">${v * 100}%</text>`;
  }
  s += `<line x1="${sx(0)}" y1="${sy(0)}" x2="${sx(1)}" y2="${sy(1)}" stroke="currentColor" stroke-opacity=".35" stroke-dasharray="4 4"/>`;
  for (const [x, y, lo, hi, n] of pts) {
    if (!isNum(x) || !isNum(y)) continue;
    if (isNum(lo) && isNum(hi)) s += `<line x1="${sx(x)}" x2="${sx(x)}" y1="${sy(lo)}" y2="${sy(hi)}" stroke="var(--accent)" stroke-width="2" stroke-opacity=".5"/>`;
    s += `<circle cx="${sx(x)}" cy="${sy(y)}" r="${3 + Math.min(6, Math.sqrt(n) / 3)}" fill="var(--accent)"><title>${n} casos: previsto ${pct(x * 100, 0)}, real ${pct(y * 100, 0)}</title></circle>`;
  }
  s += `<text x="${w / 2}" y="${h - 4}" font-size="11" text-anchor="middle" fill="currentColor">previsto</text></svg>`;
  return `<figure>${s}<figcaption>${esc(title)}</figcaption></figure>`;
}

// ------------------------------------------------------------------ aba Simulação
async function loadSimulation() {
  try {
    const s = await api("/api/simulacao");
    $("sim-loading").hidden = true;
    $("sim-wallets").innerHTML = s.carteiras.length ? s.carteiras.map(walletHtml).join("") :
      '<section class="card is-sim"><p class="muted">Nenhuma carteira virtual ainda. Rode <code>cryptoagent paper rodar</code> ou, sem internet, <code>cryptoagent --demo painel</code>.</p></section>';
    $("versions").querySelector("tbody").innerHTML = (s.versoes || []).length
      ? s.versoes.map((v) => `<tr><td>v${v.id} ${esc(v.nome)}</td><td>${v.estado === "campeao" ? "<b>campeão</b>" : esc(v.estado)}</td>
          <td class="small" title="${esc((v.motivos || []).join("; "))}">${esc(v.decisao || "-")}</td><td class="num">${v.metricas.operacoes ?? "-"}</td>
          <td class="num">${pct(v.metricas.expectativa_pct, 3, true)}</td><td class="num">${pct(v.metricas.drawdown_max_pct, 2)}</td></tr>`).join("")
      : '<tr><td colspan="6" class="muted">nenhum ciclo de aprendizado ainda (cryptoagent aprendizado reavaliar)</td></tr>';
    $("drift").hidden = !s.drift; $("drift").textContent = s.drift ? "Mudança de regime no último ciclo: " + s.drift : "";
    const cal = s.calibracao || {};
    const rules = (cal.regras || []).map((c) => [c.confianca_media / 100, c.acerto, ...(c.acerto_ic95 || []), c.casos]);
    const model = (cal.modelo || []).map((c) => [c.prob_media, c.acerto_real, ...wilson(Math.round(c.acerto_real * c.momentos), c.momentos), c.momentos]);
    $("calib").innerHTML = calibSvg(rules, "confiança das regras") + calibSvg(model, "probabilidade do modelo (fora da amostra)");
  } catch (e) {
    $("sim-loading").hidden = false; $("sim-loading").textContent = "Não foi possível carregar a simulação: " + e.message;
  }
}

function walletHtml(w) {
  const m = w.metricas || {};
  const rows = (w.operacoes || []).map((t) => `<tr><td>${when(t.entrada_ms)}</td><td>${when(t.saida_ms)}</td><td class="num">${nf(t.entrada)}</td><td class="num">${nf(t.saida)}</td>
      <td class="num">${nf(t.qtd, 4)}</td><td class="num ${t.resultado_brl < 0 ? "bad" : "ok"}">${brl(t.resultado_brl, true)}</td><td class="num">${pct(t.resultado_pct, 2, true)}</td>
      <td>${esc(t.motivo_saida)}</td><td class="small" title="${esc(t.motivo_entrada || "")}">${nf(t.confianca, 0)}% · ${esc(REGIMES[t.regime] || t.regime || "")}</td></tr>`).join("");
  const open = w.aberta ? `<p class="small">Posição virtual aberta: ${nf(w.aberta.qtd, 4)} ETH a ${brl(w.aberta.preco)} · stop ${brl(w.aberta.stop)} · alvo ${brl(w.aberta.alvo1)} · desde ${when(w.aberta.entrada_ms)}</p>` : "";
  return `<section class="card is-sim"><h2>Carteira virtual: ${esc(w.variante)} <span class="badge sim">SIMULADO</span><span class="muted small">desde ${when(w.desde_ms)}</span></h2>
    <div class="wallet-kpis">
      <div class="mini-kpi">patrimônio<b>${brl(w.patrimonio)}</b><span class="small muted">início ${brl(w.capital_inicial)}</span></div>
      <div class="mini-kpi">resultado<b class="${w.resultado_brl < 0 ? "bad" : w.resultado_brl > 0 ? "ok" : ""}">${brl(w.resultado_brl, true)}</b><span class="small muted">${pct(m.retorno_total_pct, 2, true)}</span></div>
      <div class="mini-kpi">operações<b>${m.operacoes ?? 0}</b><span class="small muted">acerto ${pct(m.taxa_acerto_pct, 0)}</span></div>
      <div class="mini-kpi">média por operação<b>${pct(m.expectativa_pct, 3, true)}</b><span class="small muted">fator de lucro ${nf(m.fator_lucro)}</span></div>
      <div class="mini-kpi">drawdown máximo<b>${pct(m.drawdown_max_pct, 2)}</b><span class="small muted">taxas ${brl(m.taxas_pagas)}</span></div>
    </div>${open}
    <h3>Curva de capital</h3>${lineSvg(w.curva, { baseline: w.capital_inicial })}
    <h3>Operações fechadas (mais recentes primeiro)</h3>
    <div class="scroll"><table class="tbl"><thead><tr><th>Entrada</th><th>Saída</th><th class="num">Preço entrada</th><th class="num">Preço saída</th><th class="num">ETH</th>
      <th class="num">Resultado</th><th class="num">%</th><th>Saída por</th><th>Confiança · regime</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="9" class="muted">nenhuma operação fechada ainda</td></tr>'}</tbody></table></div>
    ${(m.operacoes || 0) < 30 ? '<p class="small warnc">Menos de 30 operações: ainda não dá para tirar conclusões.</p>' : ""}</section>`;
}

// ------------------------------------------------------------------ aba Operações reais
async function loadJournal() {
  try {
    const j = await api("/api/diario");
    const cur = (p) => (j.moeda && j.moeda[p]) || "";
    $("journal-days").querySelector("tbody").innerHTML = j.dias.length
      ? j.dias.map((d) => `<tr><td>${esc(d.dia.split("-").reverse().join("/"))}</td><td>${esc(d.par)}</td><td class="num">${d.operacoes}</td>
          <td class="num ${d.realizado < 0 ? "bad" : d.realizado > 0 ? "ok" : ""}">${cur(d.par) === "R$" ? brl(d.realizado, true) : nf(d.realizado) + " " + esc(cur(d.par))}</td><td class="num">${nf(d.taxas)}</td></tr>`).join("")
      : '<tr><td colspan="5" class="muted">nenhuma operação registrada</td></tr>';
    $("journal-open").innerHTML = (j.posicoes || []).map((p) => `<p>Posição aberta registrada: ${nf(p.qtd, 6)} ETH em ${esc(p.par)} a custo médio ${nf(p.custo_medio)}</p>`).join("");
    $("journal-list").querySelector("tbody").innerHTML = j.operacoes.length
      ? j.operacoes.map((o) => `<tr><td>${when(o.quando_ms)}</td><td><span class="tag ${o.lado === "compra" ? "COMPRAR" : "VENDER"}">${esc(o.lado)}</span></td>
          <td class="num">${nf(o.qtd, 6)}</td><td class="num">${nf(o.preco)}</td><td class="num">${nf(o.taxa, 4)}</td>
          <td class="num ${o.resultado < 0 ? "bad" : o.resultado > 0 ? "ok" : ""}">${o.resultado === null ? "-" : nf(o.resultado)}</td><td class="small">${esc(o.nota)}</td>
          <td><button class="mini del" type="button" data-id="${o.id}">apagar</button></td></tr>`).join("")
      : '<tr><td colspan="8" class="muted">nenhum registro</td></tr>';
    document.querySelectorAll("#journal-list button.del").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm("Apagar este registro do diário? Não dá para desfazer.")) return;
      try { await api("/api/diario/remover", { method: "POST", body: JSON.stringify({ id: Number(b.dataset.id) }) }); loadJournal(); }
      catch (e) { $("journal-msg").textContent = e.message; $("journal-msg").className = "small bad"; }
    }));
  } catch (e) {
    $("journal-days").querySelector("tbody").innerHTML = `<tr><td colspan="5" class="bad">Não foi possível carregar o diário: ${esc(e.message)}</td></tr>`;
  }
}
$("journal-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("journal-msg");
  try {
    await api("/api/diario", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(e.target).entries())) });
    msg.textContent = "Registrado."; msg.className = "small ok"; e.target.reset(); loadJournal();
  } catch (err) { msg.textContent = err.message; msg.className = "small bad"; }
});

// ------------------------------------------------------------------ gráfico de candles
let chart = null, series = {};
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function initChart() {
  if (!window.LightweightCharts) { $("chart").innerHTML = '<p class="muted">Gráfico indisponível: a biblioteca não carregou. O resto do painel funciona.</p>'; return; }
  $("chart").innerHTML = "";
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
function rebuildChart() { if (chart) { chart.remove(); chart = null; series = {}; priceLines = []; } initChart(); loadCandles(); }
let priceLines = [];
async function loadCandles() {
  if (!chart) return;
  try {
    const c = await api(`/api/candles?tf=${tf}`);
    $("chart-sym").textContent = c.simbolo ? `· ${c.simbolo} ${tf}` : "";
    if (!c.candles.length) return;
    series.candles.setData(c.candles.map((r) => ({ time: r.t, open: r.open, high: r.high, low: r.low, close: r.close })));
    for (const k of ["ema_20", "ema_50", "ema_200", "bb_upper", "bb_mid", "bb_lower", "vwap"]) series[k].setData(c.candles.filter((r) => r[k] !== null).map((r) => ({ time: r.t, value: r[k] })));
    series.candles.setMarkers((c.marcadores || []).map((m) => m.acao === "COMPRAR"
      ? { time: m.t, position: "belowBar", color: css("--buy"), shape: "arrowUp", text: "COMPRAR" }
      : { time: m.t, position: "aboveBar", color: css("--sell"), shape: "arrowDown", text: "VENDER" }));
    priceLines.forEach((pl) => series.candles.removePriceLine(pl));
    priceLines = [];
    const add = (price, title, color, style = 2) => price && priceLines.push(series.candles.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title }));
    const n = c.niveis || {};
    add(n.entrada, "entrada", css("--accent")); add(n.stop, "stop", css("--sell"), 0); add(n.alvo1, "alvo 1", css("--buy")); add(n.alvo2, "alvo 2", css("--buy"));
  } catch (e) { /* tenta de novo no próximo ciclo */ }
}

// ------------------------------------------------------------------ mapa de calor
async function loadHeat() {
  try {
    const h = await api("/api/sazonalidade");
    $("heat-sym").textContent = h.simbolo ? "· " + h.simbolo : "";
    if (!h.disponivel) { $("heat").innerHTML = '<tr><td class="muted">sem perfil por horário (rode: cryptoagent sazonalidade analisar)</td></tr>'; return; }
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

// ---------------------------------------------------------------- eventos e ciclo
$("tf-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  tf = b.dataset.tf;
  document.querySelectorAll("#tf-tabs button").forEach((x) => x.classList.toggle("on", x === b));
  loadCandles();
});
$("session-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("session-msg");
  try {
    await api("/api/sessao", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(e.target).entries())) });
    msg.textContent = "Sessão salva. Reanalisando…"; msg.className = "small ok";
  } catch (err) { msg.textContent = err.message; msg.className = "small bad"; }
});
$("notif-btn").addEventListener("click", async () => {
  if (!("Notification" in window)) { $("notif-btn").textContent = "Este navegador não tem notificações"; return; }
  const p = await Notification.requestPermission();
  $("notif-btn").textContent = p === "granted" ? "Notificações ativadas ✓" : "Notificações bloqueadas";
});

async function tick() {
  try {
    render(await api("/api/estado"));
    failures = 0; $("offline").hidden = true;
  } catch (e) {
    failures += 1;
    $("pill-data").textContent = "sem conexão com o agente"; $("pill-data").className = "pill bad";
    $("offline").hidden = false;
    $("offline").textContent = `O painel não está conseguindo falar com o agente (${failures} tentativa${failures > 1 ? "s" : ""}). O agente está rodando? Tentando de novo a cada 2 s.`;
  }
}
let lastSimAnalyses = -1;
setInterval(() => {
  if (tab === "simulacao" && state && state.analises !== lastSimAnalyses) { lastSimAnalyses = state.analises; loadSimulation(); }
}, 3000);

initChart();
tick(); loadCandles(); loadHeat();
setInterval(tick, 2000);
setInterval(loadCandles, 20000);
setInterval(loadHeat, 300000);
if (params.get("aba")) showTab({ simulacao: "simulacao", reais: "reais" }[params.get("aba")] || "agora");
