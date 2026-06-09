import { API } from "./api.js";
import { connectWebSocket, appendLogLine } from "./websocket.js";

const MESES = [
  "01 - Janeiro", "02 - Fevereiro", "03 - Março", "04 - Abril",
  "05 - Maio", "06 - Junho", "07 - Julho", "08 - Agosto",
  "09 - Setembro", "10 - Outubro", "11 - Novembro", "12 - Dezembro",
];

const STATUS_SEM_ARQUIVA = new Set(["IMPORTADO", "PROCESSADO"]);
const STATUS_SEM_ESTOQUE = new Set(["IMPORTADO", "PROCESSADO"]);

let roboRodando = false;
let notasCache = [];
let xmlItens = [];

function el(id) { return document.getElementById(id); }

function showPage(name) {
  document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
  document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
  const pagina = el("page-" + name);
  if (!pagina) return;
  pagina.classList.add("active");
  document.querySelector('.nav-btn[data-page="' + name + '"]')?.classList.add("active");
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
}

function setRoboUI(rodando, msg) {
  roboRodando = rodando;
  const pill = el("robo-pill");
  const btn = el("btn-robo");
  if (!pill || !btn) return;
  pill.textContent = msg || (rodando ? "Robô em execução" : "Robô parado");
  pill.className = "status-pill" + (rodando ? " running" : "");
  btn.textContent = rodando ? "Parar robô" : "Iniciar robô";
  btn.className = "btn" + (rodando ? " danger" : " success");
}

async function refreshRoboStatus() {
  try {
    const s = await API.get("/api/robo/status");
    setRoboUI(s.rodando, s.status);
  } catch (_) {}
}

function perguntarCompraEstoque(nota) {
  return confirm(`A nota ${nota} é uma compra para estoque?`);
}

async function toggleRobo(compraEstoque = false) {
  const nota = el("robo-nota-alvo")?.value?.trim() || null;
  try {
    const r = await API.post("/api/robo/start", { nota_alvo: nota, compra_estoque: !!compraEstoque });
    appendLogLine(r.acao === "parada_solicitada" ? "Parada solicitada..." : "Robô iniciado.");
    await refreshRoboStatus();
  } catch (e) {
    alert(e.message);
  }
}

function resumirObs(texto, max = 80) {
  const t = String(texto || "").trim();
  if (!t) return "—";
  return t.length > max ? t.slice(0, max) + "…" : t;
}

function podeMarcarEstoque(status) {
  return !STATUS_SEM_ESTOQUE.has(String(status || "").toUpperCase());
}

function podeMarcarArquiva(status) {
  return !STATUS_SEM_ARQUIVA.has(String(status || "").toUpperCase());
}

function renderNotas(notas) {
  notasCache = notas;
  const tbody = el("tbody-notas");
  tbody.innerHTML = "";
  notas.forEach((n) => {
    const tr = document.createElement("tr");
    const st = (n.status || "").toUpperCase();
    if (st === "ERRO") tr.style.color = "#ff8a80";
    const chave = esc(n.chave_nfe);
    const estoqueChecked = String(n.nfe_estoque || "").includes("☑");
    const arquivaChecked = String(n.nfe_arquiva || "").includes("☑");
    const estoqueCell = podeMarcarEstoque(st)
      ? `<input type="checkbox" class="chk-estoque" data-chave="${chave}" ${estoqueChecked ? "checked" : ""} />`
      : "";
    const arquivaCell = podeMarcarArquiva(st)
      ? `<input type="checkbox" class="chk-arquiva" data-chave="${chave}" ${arquivaChecked ? "checked" : ""} />`
      : "";
    tr.innerHTML = `
      <td>${esc(n.num_nota)}</td><td>${esc(n.status)}</td><td>${esc(n.fornecedor)}</td>
      <td>${esc(n.valor)}</td><td>${esc(n.data_em)}</td><td>${esc(n.codigo_interno)}</td>
      <td class="editable" data-chave="${chave}" data-num="${esc(n.num_nota)}" data-field="placa">${esc(n.painel_placa || "—")}</td>
      <td class="editable" data-chave="${chave}" data-num="${esc(n.num_nota)}" data-field="km">${esc(n.painel_km || "—")}</td>
      <td class="center">${estoqueCell}</td><td class="center">${arquivaCell}</td>
      <td class="col-erro">${esc(n.erro_importacao)}</td>
      <td class="col-obs" data-chave="${chave}" title="Clique para ver completo">${esc(resumirObs(n.observacao_nfe))}</td>
      <td><button class="btn secondary btn-nota" data-nota="${esc(n.num_nota)}">▶ Nota</button></td>`;
    tbody.appendChild(tr);
  });
  el("info-notas").textContent = `${notas.length} nota(s) exibida(s).`;
}

function notasQueryParams() {
  return new URLSearchParams({
    dt_ini: el("f-dt-ini").value.trim(),
    dt_fim: el("f-dt-fim").value.trim(),
    cod: el("f-cod").value.trim(),
    status: el("f-status").value,
    nota: el("f-nota").value.trim(),
    limite: el("f-limite").value,
  });
}

async function buscarNotas(perguntarEstoque = false) {
  const notaFiltro = el("f-nota").value.trim();
  if (perguntarEstoque && notaFiltro && notasCache.length === 0) {
    // será verificado após busca
  }
  try {
    const r = await API.get(`/api/notas?${notasQueryParams()}`);
    renderNotas(r.notas);
    if (perguntarEstoque && notaFiltro && r.notas.length === 1) {
      const estoque = perguntarCompraEstoque(notaFiltro);
      el("robo-nota-alvo").value = notaFiltro;
      await toggleRobo(estoque);
    }
  } catch (e) {
    el("info-notas").textContent = e.message;
    el("info-notas").className = "info-bar error";
  }
}

async function editarCelula(cel) {
  const field = cel.dataset.field;
  const chave = cel.dataset.chave;
  const num = cel.dataset.num;
  const atual = cel.textContent === "—" ? "" : cel.textContent;
  const novo = prompt(field === "placa" ? "Placa:" : "KM:", atual);
  if (novo === null) return;
  try {
    await API.patch(`/api/notas/${field}`, { chave_nfe: chave, num_nota: num, valor: novo.trim() });
    cel.textContent = novo.trim() || "—";
  } catch (e) { alert(e.message); }
}

async function toggleFlag(chave, tipo, valor) {
  try {
    await API.patch(`/api/notas/${tipo}`, { chave_nfe: chave, valor });
  } catch (e) {
    alert(e.message);
    buscarNotas();
  }
}

function perguntarFormatoRelatorio() {
  const pdf = confirm("OK = PDF (HTML)\nCancelar = Excel");
  return pdf ? "html" : "excel";
}

function relatorioNotas() {
  const q = notasQueryParams();
  const fmt = perguntarFormatoRelatorio();
  API.openInNewTab(`/api/relatorios/notas/${fmt}?${q}`);
}

// --- XML ---
function renderXml(itens) {
  xmlItens = itens;
  const tbody = el("tbody-xml");
  tbody.innerHTML = "";
  itens.forEach((item) => {
    const tr = document.createElement("tr");
    tr.dataset.caminho = item.caminho || "";
    tr.innerHTML = `
      <td>${esc(item.numero_nota)}</td>
      <td>${esc(item.arquivo)}</td>
      <td class="xml-status">${esc(item.status || "PENDENTE")}</td>
      <td class="xml-msg">${esc(item.mensagem || "")}</td>`;
    tbody.appendChild(tr);
  });
  el("info-xml").textContent = `${itens.length} XML(s) carregado(s).`;
}

async function carregarPastaXml() {
  const pasta = el("xml-pasta").value.trim();
  if (!pasta) { alert("Informe o caminho da pasta."); return; }
  try {
    const r = await API.post("/api/importa-xml/listar-pasta", { caminho: pasta });
    renderXml(r.itens);
  } catch (e) { alert(e.message); }
}

async function uploadXmls(ev) {
  const files = ev.target.files;
  if (!files?.length) return;
  const fd = new FormData();
  for (const f of files) fd.append("arquivos", f);
  try {
    const r = await API.upload("/api/importa-xml/upload", fd);
    renderXml(r.itens);
    el("xml-pasta").value = r.pasta || "";
  } catch (e) { alert(e.message); }
}

async function iniciarImportacaoXml() {
  const caminhos = xmlItens.map((i) => i.caminho).filter(Boolean);
  if (!caminhos.length) { alert("Nenhum XML carregado."); return; }
  try {
    const r = await API.post("/api/importa-xml/iniciar", { caminhos });
    appendLogLine(r.mensagem || "Importação XML iniciada.");
  } catch (e) { alert(e.message); }
}

// --- Veículos ---
function renderVeiculos(veiculos, ultima) {
  const tbody = el("tbody-veiculos");
  tbody.innerHTML = "";
  veiculos.forEach((v) => {
    const tr = document.createElement("tr");
    if (v.carreta_duplicada) tr.classList.add("dup");
    tr.innerHTML = `
      <td>${esc(v.codVeiculo)}</td><td>${esc(v.cavalo)}</td><td>${esc(v.placa)}</td>
      <td>${esc(v.carreta1)}</td><td>${esc(v.carreta2)}</td><td>${esc(v.carreta3)}</td>
      <td>${esc(v.veiculoProprio)}</td>
      <td style="white-space:normal;max-width:320px">${esc(v.movimentacao_carreta)}</td>
      <td>${esc(v.data_movimentacao)}</td>`;
    tbody.appendChild(tr);
  });
  el("info-veiculos").textContent = `${veiculos.length} veículo(s). Última sync: ${ultima || "—"}`;
}

async function buscarVeiculos() {
  const q = new URLSearchParams({ limite: el("v-limite").value, placa: el("v-placa").value.trim() });
  try {
    const r = await API.get(`/api/veiculos?${q}`);
    renderVeiculos(r.veiculos, r.ultima_sync);
  } catch (e) { el("info-veiculos").textContent = e.message; }
}

async function syncVeiculos() {
  el("btn-sync-veiculos").disabled = true;
  try {
    await API.post("/api/veiculos/sync");
    appendLogLine("Sincronização de frota iniciada...");
  } finally {
    setTimeout(() => { el("btn-sync-veiculos").disabled = false; }, 2000);
  }
}

// --- Itens ---
function itensQueryParams() {
  return new URLSearchParams({
    cod: el("i-cod").value.trim(),
    grupo: el("i-grupo").value,
    descricao: el("i-desc").value.trim(),
    limite: el("i-limite").value,
  });
}

function renderItens(itens) {
  const tbody = el("tbody-itens");
  tbody.innerHTML = "";
  itens.forEach((i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="checkbox" class="chk-item" data-cod="${esc(i.codItemD)}" /></td>
      <td>${esc(i.codItemD)}</td><td>${esc(i.descricao)}</td>
      <td>${esc(i.descGrupoImp)}</td><td>${esc(i.descNegocioImp)}</td>`;
    tbody.appendChild(tr);
  });
  el("info-itens").textContent = `${itens.length} item(ns) exibido(s).`;
}

async function carregarGrupos() {
  try {
    const r = await API.get("/api/itens/grupos");
    const sel = el("i-grupo");
    const atual = sel.value;
    sel.innerHTML = "";
    (r.grupos || ["Todos"]).forEach((g) => {
      const o = document.createElement("option");
      o.value = g; o.textContent = g;
      sel.appendChild(o);
    });
    if (atual) sel.value = atual;
  } catch (_) {}
}

async function buscarItens() {
  try {
    const r = await API.get(`/api/itens?${itensQueryParams()}`);
    renderItens(r.itens);
  } catch (e) { el("info-itens").textContent = e.message; }
}

async function syncItens() {
  await API.post("/api/itens/sync");
  appendLogLine("Sincronização de itens iniciada...");
}

function relatorioItens() {
  const q = itensQueryParams();
  const fmt = perguntarFormatoRelatorio();
  API.openInNewTab(`/api/relatorios/itens/${fmt}?${q}`);
}

async function migrarItens() {
  const cods = [...document.querySelectorAll(".chk-item:checked")].map((c) => c.dataset.cod);
  if (!cods.length) { alert("Marque ao menos um item."); return; }
  const novoGrupo = prompt("Novo grupo (nome exato no ERP):");
  if (!novoGrupo) return;
  try {
    const r = await API.post("/api/itens/migrar", { codigos: cods, novo_grupo: novoGrupo });
    appendLogLine(r.mensagem || "Migração iniciada.");
  } catch (e) { alert(e.message); }
}

// --- Filtros ---
async function carregarFiltros() {
  const r = await API.get("/api/filtros");
  const f = r.filtros || {};
  const comb = r.combustiveis || {};
  const rel = r.relatorios || {};
  el("filtro-mes").value = f.mes || MESES[0];
  el("filtro-ano").value = f.ano || "2026";
  el("filtro-filial").value = f.cod_filial || "";
  el("filtro-ue").value = f.cod_unidade_embarque || "";
  el("filtro-30").checked = !!f.ultimos_30_dias;
  el("filtro-hoje").checked = !!f.hoje_apenas;
  el("filtro-forn").value = f.fornecedores_fatura_afaturar || "";
  el("filtro-tipo-forn").value = f.cod_tipo_fornecedor || "";
  el("filtro-placas").value = r.placas || "";
  el("filtro-km").value = r.km || "";
  el("filtro-etanol").value = comb.etanol || "";
  el("filtro-gasolina").value = comb.gasolina || "";
  el("filtro-s10").value = comb.s10 || "";
  el("filtro-s500").value = comb.s500 || "";
  el("filtro-arla").value = comb.arla || "";
  el("filtro-rel-veiculo").value = rel.rel_veiculo || "";
  el("filtro-rel-item").value = rel.rel_item || "";
  el("filtro-rel-grupo").value = rel.cod_grupo_item || "";
}

async function salvarFiltros() {
  try {
    await API.put("/api/filtros", {
      mes: el("filtro-mes").value,
      ano: el("filtro-ano").value,
      cod_filial: el("filtro-filial").value,
      cod_unidade_embarque: el("filtro-ue").value,
      ultimos_30_dias: el("filtro-30").checked,
      hoje_apenas: el("filtro-hoje").checked,
      fornecedores_fatura_afaturar: el("filtro-forn").value,
      cod_tipo_fornecedor: el("filtro-tipo-forn").value,
      modelos_placa: el("filtro-placas").value,
      modelos_km: el("filtro-km").value,
      cod_etanol: el("filtro-etanol").value,
      cod_gasolina: el("filtro-gasolina").value,
      cod_s10: el("filtro-s10").value,
      cod_s500: el("filtro-s500").value,
      cod_arla: el("filtro-arla").value,
      rel_veiculo: el("filtro-rel-veiculo").value,
      rel_item: el("filtro-rel-item").value,
      cod_grupo_item: el("filtro-rel-grupo").value,
    });
    alert("Parâmetros salvos.");
  } catch (e) { alert(e.message); }
}

// --- Config ---
async function carregarConfig() {
  const r = await API.get("/api/config");
  const c = r.config || {};
  const setVal = (id, val) => { const n = el(id); if (n) n.value = val ?? ""; };
  const setChk = (id, val) => { const n = el(id); if (n) n.checked = !!val; };
  setVal("cfg-link", c.link);
  setVal("cfg-user", c.user_sis);
  setVal("cfg-senha", c.senha_sis);
  setVal("cfg-smtp", c.smtp);
  setVal("cfg-porta", c.porta);
  setChk("cfg-ssl", c.ssl);
  setVal("cfg-email", c.user_email);
  setVal("cfg-email-senha", c.senha_email);
  setVal("cfg-destinatarios", c.destinatarios);
  setVal("cfg-agendamento", c.agendamento_tipo || "");
  setVal("cfg-intervalo", c.intervalo_horas || 1);
  if (el("cfg-agendamento-resumo")) el("cfg-agendamento-resumo").textContent = r.agendamento_resumo || "";
  if (el("cfg-versao")) el("cfg-versao").textContent = `Versão: ${r.versao || ""}`;
  if (el("brand-versao")) el("brand-versao").textContent = r.versao || "";
  await carregarLicenca();
}

async function carregarLicenca() {
  try {
    const r = await API.get("/api/licenca/status");
    setVal("cfg-razao", r.razao_social);
    setVal("cfg-instalacao-id", r.instalacao_id);
    const st = el("cfg-licenca-status");
    if (st) {
      st.textContent = r.configurada
        ? (r.liberada ? "Licença ativa." : "Aguardando liberação no GitHub.")
        : "Licenciamento remoto não configurado (licenca_config.py).";
      st.className = "hint " + (r.liberada ? "ok" : "warn");
    }
  } catch (_) {}
}

function setVal(id, val) { const n = el(id); if (n) n.value = val ?? ""; }

async function salvarConfig() {
  try {
    await API.put("/api/config", {
      link: el("cfg-link")?.value || "",
      user_sis: el("cfg-user")?.value || "",
      senha_sis: el("cfg-senha")?.value || "",
      smtp: el("cfg-smtp")?.value || "",
      user_email: el("cfg-email")?.value || "",
      senha_email: el("cfg-email-senha")?.value || "",
      ssl: el("cfg-ssl")?.checked ? 1 : 0,
      porta: el("cfg-porta")?.value || "",
      destinatarios: el("cfg-destinatarios")?.value || "",
      agendamento_tipo: el("cfg-agendamento")?.value || "",
      intervalo_horas: parseInt(el("cfg-intervalo")?.value || "1", 10),
    });
    alert("Configurações salvas.");
    await carregarConfig();
  } catch (e) { alert(e.message); }
}

async function enviarRelatorioManual() {
  try {
    const r = await API.post("/api/email/enviar-manual");
    alert(`Relatório enviado: ${r.total_notas} notas, ${r.total_itens} itens.`);
  } catch (e) { alert(e.message); }
}

async function registrarLicenca() {
  try {
    const r = await API.post("/api/licenca/registrar", { razao_social: el("cfg-razao").value });
    alert(r.mensagem || "Licença registrada.");
    await carregarLicenca();
  } catch (e) { alert(e.message); }
}

async function verificarLicenca() {
  try {
    const r = await API.post("/api/licenca/verificar");
    alert(r.mensagem);
    await carregarLicenca();
  } catch (e) { alert(e.message); }
}

// --- Logs ---
function logsQueryParams() {
  return new URLSearchParams({
    limite: el("log-limite").value,
    dt_ini: el("log-dt-ini").value.trim(),
    dt_fim: el("log-dt-fim").value.trim(),
    nota: el("log-nota").value.trim(),
  });
}

async function carregarLogs() {
  try {
    const r = await API.get(`/api/logs?${logsQueryParams()}`);
    const panel = el("log-page-content");
    panel.innerHTML = "";
    r.logs.forEach((l) => {
      const div = document.createElement("div");
      div.className = "log-line" + (l.nivel === "ERRO" ? " erro" : "");
      div.textContent = `[${l.criado_em}] ${l.mensagem}`;
      panel.appendChild(div);
    });
    panel.scrollTop = panel.scrollHeight;
  } catch (e) { alert(e.message); }
}

async function limparLogs() {
  if (!confirm("Limpar todos os logs?")) return;
  await API.delete("/api/logs");
  carregarLogs();
}

async function enviarSuporte() {
  const dtIni = prompt("Data inicial (DD/MM/AAAA):", new Date().toLocaleDateString("pt-BR"));
  if (!dtIni) return;
  const dtFim = prompt("Data final (DD/MM/AAAA):", dtIni);
  if (!dtFim) return;
  try {
    const r = await API.post("/api/suporte/enviar-log", { dt_ini: dtIni, dt_fim: dtFim });
    alert(`Enviado: ${r.qtd_logs} logs, ${r.qtd_notas} notas.`);
  } catch (e) { alert(e.message); }
}

// --- Nav & Events ---
function initNav() {
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const page = btn.dataset.page;
      showPage(page);
      try {
        if (page === "execucao") await buscarNotas();
        if (page === "veiculos") await buscarVeiculos();
        if (page === "itens") { await carregarGrupos(); await buscarItens(); }
        if (page === "filtros") await carregarFiltros();
        if (page === "config") await carregarConfig();
        if (page === "logs") await carregarLogs();
      } catch (e) { alert("Erro ao carregar: " + e.message); }
    });
  });
}

function bindClick(id, fn) { const n = el(id); if (n) n.onclick = fn; }

function initEvents() {
  bindClick("btn-buscar-notas", () => buscarNotas(true));
  bindClick("btn-limpar-notas", () => {
    ["f-dt-ini", "f-dt-fim", "f-cod", "f-nota"].forEach((id) => { const n = el(id); if (n) n.value = ""; });
    el("f-status").value = "Todos";
    buscarNotas();
  });
  bindClick("btn-rel-notas", relatorioNotas);
  bindClick("btn-robo", () => toggleRobo(false));
  bindClick("btn-sync-veiculos", syncVeiculos);
  bindClick("btn-buscar-veiculos", buscarVeiculos);
  bindClick("btn-sync-itens", syncItens);
  bindClick("btn-buscar-itens", buscarItens);
  bindClick("btn-migrar-itens", migrarItens);
  bindClick("btn-rel-itens", relatorioItens);
  bindClick("btn-salvar-filtros", salvarFiltros);
  bindClick("btn-salvar-config", salvarConfig);
  bindClick("btn-enviar-relatorio", enviarRelatorioManual);
  bindClick("btn-registrar-licenca", registrarLicenca);
  bindClick("btn-verificar-licenca", verificarLicenca);
  bindClick("btn-filtrar-logs", carregarLogs);
  bindClick("btn-limpar-filtros-logs", () => {
    ["log-dt-ini", "log-dt-fim", "log-nota"].forEach((id) => { const n = el(id); if (n) n.value = ""; });
    carregarLogs();
  });
  bindClick("btn-limpar-logs", limparLogs);
  bindClick("btn-enviar-suporte", enviarSuporte);
  bindClick("btn-xml-pasta", carregarPastaXml);
  bindClick("btn-xml-iniciar", iniciarImportacaoXml);

  const xmlUpload = el("xml-upload");
  if (xmlUpload) xmlUpload.onchange = uploadXmls;

  const checkAll = el("i-check-all");
  if (checkAll) {
    checkAll.onchange = () => {
      document.querySelectorAll(".chk-item").forEach((c) => { c.checked = checkAll.checked; });
    };
  }

  const tbodyNotas = el("tbody-notas");
  if (tbodyNotas) {
    tbodyNotas.addEventListener("click", (e) => {
      const obs = e.target.closest(".col-obs");
      if (obs) {
        const nota = notasCache.find((n) => n.chave_nfe === obs.dataset.chave);
        const texto = String(nota?.observacao_nfe || "").trim();
        if (texto) alert("Observação da NFe:\n\n" + texto);
        return;
      }
      const cel = e.target.closest(".editable");
      if (cel) editarCelula(cel);
      const btn = e.target.closest(".btn-nota");
      if (btn) {
        const nota = btn.dataset.nota;
        const estoque = perguntarCompraEstoque(nota);
        el("robo-nota-alvo").value = nota;
        toggleRobo(estoque);
      }
    });
    tbodyNotas.addEventListener("change", (e) => {
      if (e.target.classList.contains("chk-estoque")) {
        toggleFlag(e.target.dataset.chave, "estoque", e.target.checked);
      }
      if (e.target.classList.contains("chk-arquiva")) {
        toggleFlag(e.target.dataset.chave, "arquiva", e.target.checked);
      }
    });
  }
}

function initWebSocket() {
  connectWebSocket((msg) => {
    if (msg.tipo === "log") appendLogLine(msg.mensagem, msg.nivel);
    if (msg.tipo === "status") setRoboUI(msg.rodando, msg.mensagem);
    if (msg.tipo === "painel_atualizar") buscarNotas();
    if (msg.tipo === "frota_atualizada") buscarVeiculos();
    if (msg.tipo === "itens_atualizados") { carregarGrupos(); buscarItens(); }
    if (msg.tipo === "xml_item") {
      const row = [...document.querySelectorAll("#tbody-xml tr")].find(
        (tr) => tr.dataset.caminho === msg.caminho,
      );
      if (row) {
        row.querySelector(".xml-status").textContent = msg.status;
        row.querySelector(".xml-msg").textContent = msg.mensagem || "";
      }
    }
  });
}

async function init() {
  const mesSel = el("filtro-mes");
  if (mesSel) {
    MESES.forEach((m) => {
      const o = document.createElement("option");
      o.value = m; o.textContent = m;
      mesSel.appendChild(o);
    });
  }
  initNav();
  initEvents();
  initWebSocket();
  showPage("execucao");
  await refreshRoboStatus();
  await buscarNotas();
  setInterval(refreshRoboStatus, 5000);
  try {
    const h = await API.get("/api/health");
    const sub = el("brand-sub");
    if (sub) sub.textContent = `Web autônomo · ${(h.banco || "").split(/[/\\]/).pop() || "PostgreSQL"}`;
    const cfg = await API.get("/api/config");
    if (el("brand-versao")) el("brand-versao").textContent = cfg.versao || "";
  } catch (_) {}
}

init().catch((e) => { console.error(e); alert("Erro ao iniciar: " + e.message); });
