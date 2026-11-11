# PROGRESSO — IA_para_Criptos_2.0

Checklist vivo da revisão do projeto. Atualizado a cada item concluído.
Legenda: `[ ]` pendente · `[x]` concluído com evidência · `[BLOQUEADO]` · `[ADIADO]`

**Regra:** uma etapa por vez; só avanço com "aprovado" ou "próxima etapa".
**Ao retomar com contexto limpo:** reler este arquivo inteiro antes de continuar.

| Etapa | Nome | Estado |
|---|---|---|
| 0 | Preparação | concluída e aprovada (2026-11-10) |
| 1 | Diagnóstico | concluída — aguardando aprovação |
| 2 | Segurança e configuração | pendente |
| 3 | Estrutura e qualidade do código | pendente |
| 4 | Coleta de dados | pendente |
| 5 | Performance e eficiência | pendente |
| 6 | Modelo, backtest e confiabilidade | pendente |
| 7 | Simulação com dinheiro virtual | pendente |
| 8 | Aprendizado contínuo e panoramas | pendente |
| 9 | Controle de risco e sinais | pendente |
| 10 | Dashboard e aparência | pendente |
| 11 | Testes, lint e CI | pendente |
| 12 | Documentação para o GitHub | pendente |
| 13 | LinkedIn, changelog e auditoria final | pendente |

---

## ETAPA 0 — PREPARAÇÃO

- [x] **0.1** Verificar se existe repositório git; se não, `git init` e commit inicial do estado atual.
  - 2026-11-10: `git rev-parse --is-inside-work-tree` → `fatal: not a git repository`; `git init -b main` → `Initialized empty Git repository in C:/Users/breno/Documents/IA_para_Criptos_2.0/.git/`.
  - Conferência antes do commit: `git add -A --dry-run` não lista `.env`, `data/`, `logs/` nem `.venv/`; `git check-ignore -v` confirma as regras (`.gitignore:2:.env`, `.gitignore:14:data/`, `.gitignore:15:logs/`, `.gitignore:7:.venv/`).
  - O git não tinha `user.name`/`user.email`. Configurado só neste repositório: `BrenoBorgesss` / `BrenoBorgesss@users.noreply.github.com`.
  - Commit inicial: `8ab7665 chore: commit inicial do estado atual do projeto` — 99 arquivos. Filtro dos arquivos staged por `.env`, `data/`, `logs/`, `.venv/`, `.sqlite`, `.parquet` → nenhuma ocorrência.
  - Aviso do git: "LF will be replaced by CRLF" em todos os arquivos (falta `.gitattributes`; tratar na Etapa 11.2).
- [x] **0.2** Criar a branch `melhorias`.
  - `git switch -c melhorias` → `Switched to a new branch 'melhorias'`; `git log --oneline --all --decorate` → `8ab7665 (HEAD -> melhorias, main)`.
- [x] **0.3** Criar o `PROGRESSO.md` com todas as etapas (0 a 13) e os itens de cada uma.
  - Evidência: este arquivo.
- [x] **0.4** Criar o ambiente virtual e confirmar a versão da linguagem e do gerenciador de pacotes.
  - Já existia `.venv/` (criado em 22/10/2026 com `python -m venv`, conforme `.venv/pyvenv.cfg`). Reaproveitado, não recriado.
  - `.venv\Scripts\python.exe --version` → `Python 3.14.7`
  - `.venv\Scripts\python.exe -m pip --version` → `pip 26.2.2 ... (python 3.14)`
  - `pip check` → `No broken requirements found.`
  - 38 pacotes instalados. Diretos (declarados em `pyproject.toml`): cryptoagent 0.1.0 (o próprio projeto), fastapi 0.159.1, httpx 0.28.1, numpy 2.5.4, pandas 3.0.6, pyarrow 26.0.0, pydantic 2.14.1, pytest 9.1.1, python-dotenv 1.2.4, PyYAML 6.0.3, rich 15.0.0, tzdata 2026.5, uvicorn 0.54.0, websockets 17.3.
  - Dependências registradas em `pyproject.toml` (com mínimos, sem versões travadas — travar é assunto da Etapa 12.2).
- [x] **0.5** Listar a árvore de arquivos do projeto.
  - Evidência: seção "Árvore de arquivos" abaixo (156 arquivos fora de `.git`, `.venv` e `__pycache__`).

### Árvore de arquivos (2026-11-10, antes de qualquer alteração)

```
.env                    (663 B — IGNORADO pelo git; conteúdo NÃO foi lido)
.env.example            (535 B)
.gitignore              (235 B)
pyproject.toml          (838 B)
README.md               (14 KB)
atalhos/                iniciar_agente.ps1, ver_relatorio.ps1
config/                 config.yaml (10,5 KB), estudo_travas.yaml, hipotese_setup_1h.yaml
data/                   (IGNORADO pelo git)
  agent.sqlite                                   13,3 MB
  agent_backup_antes_reset_estudo_20261023.sqlite 6,2 MB
  costs.sqlite                                   44,4 MB
  history/ETHBRL/   1m, 5m, 15m, 1h              (.parquet + .manifest.json)
  history/ETHUSDT/  1m, 5m, 15m, 1h, 4h, 1d      (.parquet + .manifest.json)
  history/USDTBRL/  1m, 5m, 15m, 1h              (.parquet + .manifest.json)
  reports/          backtest.html/.json, sazonalidade.html/.json, hipotese_setup_1h/backtest.html/.json
logs/                   agente.log (IGNORADO pelo git)
src/cryptoagent/
  __init__.py, __main__.py, cli.py (71,8 KB), config.py (18,1 KB), fmt.py, logging_setup.py, timeutils.py
  account/      binance_account.py, journal.py, ledger.py, limits.py, permissions.py, status.py
  alerts/       notify.py
  backtest/     baseline.py, costs.py, data.py, execution.py, metrics.py, report.py, runner.py, season_pit.py, simulator.py
  costs/        orderbook.py, recorder.py, roundtrip.py, summary.py
  dashboard/    server.py, service.py, static/{app.js, index.html, style.css}
  data/         health.py, history.py, market_store.py, models.py, rest.py, websocket.py
  engine/       context.py, decision.py, journal.py, live.py, rules.py, signals.py
  indicators/   compute.py, core.py, momentum.py, regime.py, structure.py, trend.py, volatility.py, volume.py
  paper/        book.py, report.py, runner.py
  seasonality/  analysis.py, compare.py, cost_model.py, profiles.py, validation.py
  security/     redact.py
  session/      daily.py
  storage/      agent_db.py, db.py
src/cryptoagent.egg-info/   (IGNORADO; gerado pela instalação editável)
tests/                  conftest.py + 19 arquivos test_*.py
.pytest_cache/, .venv/  (IGNORADOS)
```

### Observações da Etapa 0 (fatos vistos, sem diagnóstico ainda)

- Linguagem: Python (`requires-python = ">=3.11"`), pacote `cryptoagent` em layout `src/`, entrada `cryptoagent = "cryptoagent.cli:main"`.
- Já existem `.gitignore` e `.env.example`. O `.env` real existe e está ignorado; não foi aberto.
- Busca por padrões de segredo nos arquivos que entrarão no commit (sem imprimir valores): única ocorrência é `tests/test_dashboard.py:15`, um token fictício de teste (`"token-de-teste-..."`). Nenhuma sequência de 64 caracteres alfanuméricos (formato de chave Binance) em `src/`, `tests/`, `config/` ou `atalhos/`. A varredura completa (incluindo conteúdo de `config/*.yaml` e do README) é o item 1.3.
- `data/` contém bancos SQLite que podem ter dados pessoais (saldos/operações). Estão ignorados e ficam fora do repositório.

### Decisões

- Branch inicial criada como `main`; o trabalho acontece em `melhorias`.
- Autor dos commits: e-mail noreply do GitHub, configurado só neste repositório (escolha do usuário em 2026-11-10).
- O `.venv` existente foi reaproveitado.

### Pendências / dúvidas da Etapa 0

- Nenhuma pendência. Aguardando aprovação para a Etapa 1.
- Dúvida registrada para a Etapa 3.1: o projeto já é modular (`src/cryptoagent/...`) com nomes diferentes dos pedidos (`engine/`, `paper/`, `indicators/` em vez de `models/`, `simulation/`, `features/`). O diagnóstico (1.5) vai propor se vale renomear ou manter.

---

## ETAPA 1 — DIAGNÓSTICO (somente leitura)

- [x] **1.1** Mapear estrutura, linguagem, dependências, pontos de entrada e fluxo de dados (coleta → processamento → modelo → sinal → simulação → dashboard), citando os arquivos reais.
  - Evidência: `DIAGNOSTICO.md`, seção 1.1 (tabela fase → arquivos). 74 arquivos `.py`, 9.007 linhas em `src/cryptoagent` (medido por script AST).
- [x] **1.2** Rodar o projeto e os testes existentes; registrar erros, avisos e tempos reais.
  - `python -m pytest -q --durations=10` → `213 passed, 1 warning in 23.07s` (25,79 s de relógio). Aviso: `StarletteDeprecationWarning` do TestClient.
  - `cryptoagent --help` 1,58 s · `historico verificar` 1,39 s (0 buracos em 14 séries) · `custos resumo` 1,35 s (6 dias de coleta) · `dados status` 9,80 s ("Dados em ordem") · `paper relatorio` 12 s (0 operações virtuais fechadas).
  - `cryptoagent backtest --rapido --pares ETHBRL` (relatório gravado no scratchpad, não em `data/reports`): 13 operações, −3,67%, drawdown −4,45%, acerto 23,1%, Sharpe −2,33; comprar e segurar +24,07%; 32 s.
  - Erro observado: leitura de taxas da conta recusada pela Binance (código −2015); tratado pelo programa.
  - NÃO executados, de propósito (exibem saldo/operações reais ou gravam nos bancos do usuário): `analisar`, `conta *`, `painel`, `sessao`, `sinais`, `paper rodar`, `custos coletar`, `historico baixar`, `sazonalidade analisar`. O painel não foi aberto no navegador.
- [x] **1.3** Procurar chaves/segredos no código e no histórico git (sem imprimir os valores).
  - `git grep` em todos os commits com 7 padrões (chave de 64 caracteres, `sk-ant-`, `AKIA`, PEM, atribuição de segredo, token GitHub, CPF): só valores fictícios de teste (`tests/test_redact.py`, `tests/test_dashboard.py:15`) e o marcador `__TOKEN__` em `index.html:137`.
  - `.env`, `data/`, `logs/` nunca versionados (0 commits). `.env.example` sem valores. Autor: só e-mail noreply.
  - Achado: caminho pessoal do Windows em `README.md:22,29,145` (corrigir na Etapa 12). Nenhuma chave a revogar.
- [x] **1.4** Gerar `DIAGNOSTICO.md` com problemas por categoria (bugs, segurança, performance, qualidade, coleta de dados, confiabilidade do modelo, simulação/aprendizado, UI/UX, documentação), cada um com arquivo/linha, prioridade e como corrigir.
  - Evidência: `DIAGNOSTICO.md`, seção 1.4. 62 problemas (9 alta, 31 média, 22 baixa), contados por script sobre as tabelas. Commit `35fced1`.
- [x] **1.5** Propor o plano detalhado das etapas 2 a 13 ajustado ao que foi encontrado.
  - Evidência: `DIAGNOSTICO.md`, seção 1.5. Inclui 4 decisões pendentes do usuário (D1 a D4).

### Achados principais da Etapa 1
1. O projeto já é um pacote maduro (213 testes, cliente somente leitura, config validada). Muitas tarefas do roteiro viram "confirmar", não "fazer".
2. Não existe modelo treinado/adaptativo: a decisão é por regras fixas. A Etapa 8 é construção nova.
3. A estratégia não tem vantagem comprovada (retorno negativo fora da amostra, 13 a 18 operações).
4. O backtest não é reprodutível hoje: depende da taxa lida da conta e do `costs.sqlite`, que cresce (−3,652% no relatório salvo × −3,67% hoje).
5. A chave da Binance está sendo recusada (−2015); ação do usuário.

### Decisões pendentes (pedir ao usuário)
- **D1** Manter os nomes atuais das pastas (`engine/`, `paper/`, `indicators/`) em vez de renomear? Recomendação: manter.
- **D2** Introduzir um modelo simples (regressão logística, `scikit-learn`) como "desafiador" das regras atuais? Recomendação: sim.
- **D3** Etapas 2 a 8 sem depender da chave; 9.3 testado com respostas simuladas. Recomendação: sim.
- **D4** Remover `ANTHROPIC_API_KEY` (não usada)? Recomendação: sim.
- Proposta de ordem: antecipar o congelamento de custos do backtest (M3) para o início da Etapa 3, pois é o baseline de comparação do item 3.4.

### Problemas durante a Etapa 1
- O script que atualizaria este arquivo falhou por erro de escape; o commit `35fced1` levou só o `DIAGNOSTICO.md`. Este arquivo foi atualizado em um commit separado.

### Limites da Etapa 1
- Arquivos não lidos estão listados no fim do `DIAGNOSTICO.md`; serão lidos na etapa que os tocar.
- Lint e verificador de tipos não rodados (não instalados; etapa somente leitura).

## ETAPA 2 — SEGURANÇA E CONFIGURAÇÃO

- [ ] **2.1** Remover/mover segredos para variáveis de ambiente; criar `.env.example` e `.gitignore`.
- [ ] **2.2** Garantir modo somente leitura da Binance e a trava de execução real desativada por padrão.
- [ ] **2.3** Criar configuração centralizada (YAML/.env) e eliminar números mágicos.
- [ ] **2.4** Adicionar logging estruturado no lugar de print.

## ETAPA 3 — ESTRUTURA E QUALIDADE DO CÓDIGO

- [ ] **3.1** Reorganizar em módulos (`data/`, `features/`, `models/`, `backtest/`, `simulation/`, `learning/`, `dashboard/`, `config/`, `tests/`), sem quebrar o que já funciona.
- [ ] **3.2** Adicionar type hints e docstrings.
- [ ] **3.3** Remover código morto e duplicação.
- [ ] **3.4** Confirmar que o projeto continua rodando igual ao baseline da Etapa 1 (comparar saídas).

## ETAPA 4 — COLETA DE DADOS

- [ ] **4.1** Módulo central de coleta com timeout, retry com backoff exponencial e tratamento de rate limit.
- [ ] **4.2** Validação dos dados (nulos, duplicados, timestamps fora de ordem, outliers, lacunas de candles).
- [ ] **4.3** Cache local (SQLite ou parquet) com coleta incremental.
- [ ] **4.4** Valores em R$ (par ETH/BRL ou conversão confiável) e timezone consistente.
- [ ] **4.5** Modo offline/demonstração com dados históricos de exemplo (sem dados pessoais).

## ETAPA 5 — PERFORMANCE E EFICIÊNCIA

- [ ] **5.1** Medir o baseline (tempo de inicialização, coleta, cálculo de indicadores, memória) com profiling real.
- [ ] **5.2** Corrigir gargalos (vetorização, cache de indicadores, evitar recálculo, coleta assíncrona/em segundo plano).
- [ ] **5.3** Medir de novo e registrar a tabela antes/depois com números reais.

## ETAPA 6 — MODELO, BACKTEST E CONFIABILIDADE

- [ ] **6.1** Auditar vazamento de dados, look-ahead bias e overfitting; documentar o que foi achado.
- [ ] **6.2** Backtest com validação walk-forward (nunca embaralhar séries temporais), com taxas e slippage realistas.
- [ ] **6.3** Métricas: retorno, win rate, drawdown máximo, Sharpe, nº de operações, comparação com buy-and-hold.
- [ ] **6.4** Regra de confiança: ordem firme só acima do limiar configurável; senão "AGUARDAR".
- [ ] **6.5** Reprodutibilidade (seed fixa, parâmetros versionados, modelo salvo).

## ETAPA 7 — SIMULAÇÃO COM DINHEIRO VIRTUAL

- [ ] **7.1** Carteira virtual configurável (saldo inicial em R$ e perda máxima do dia definidos pelo usuário), separada da real.
- [ ] **7.2** Operar com dados de mercado reais (somente leitura) no perfil intraday/spot/reversão à média.
- [ ] **7.3** Execução realista: taxas, slippage, spread, tamanho mínimo de ordem, atraso de execução.
- [ ] **7.4** Registrar TODA operação simulada em SQLite (horário, preço, quantidade, motivo, features, confiança, taxa, resultado em R$, cenário de mercado).
- [ ] **7.5** Modo replay com dados históricos acelerados, reset da simulação e comparação com buy-and-hold.

## ETAPA 8 — APRENDIZADO CONTÍNUO E PANORAMAS CONFIÁVEIS

- [ ] **8.1** Ciclo de reavaliação periódica (fim do dia ou a cada N operações) que ajusta/retreina com os dados da simulação.
- [ ] **8.2** Esquema campeão/desafiador: o modelo novo só substitui o atual se for melhor fora da amostra (walk-forward), depois de custos, sem aumentar o drawdown.
- [ ] **8.3** Histórico de versões do modelo com métricas.
- [ ] **8.4** Proteções: mínimo de operações/dias antes de confiar, detecção de drift/mudança de regime, redução de confiança em cenários inéditos.
- [ ] **8.5** Panoramas: probabilidade de acerto, ganho/perda esperados, intervalo de confiança e desempenho por cenário; calibração da confiança (com gráfico).
- [ ] **8.6** Critérios de promoção configuráveis; enquanto não atingidos, mostrar "AGUARDAR / EM TESTE".

## ETAPA 9 — CONTROLE DE RISCO E SINAIS

- [ ] **9.1** Capital inicial e perda máxima do dia informados pelo usuário no início do dia.
- [ ] **9.2** Bloqueio de novos sinais ao atingir o limite (simulação e sinais reais).
- [ ] **9.3** Posição lida pelo saldo de ETH (chave somente leitura); VENDER = sair da posição.
- [ ] **9.4** Testes automatizados dessas regras.

## ETAPA 10 — DASHBOARD E APARÊNCIA

- [ ] **10.1** Visual moderno e responsivo, tema escuro/claro, candles com indicadores, sinais marcados, cards (preço em R$, posição, sinal, confiança).
- [ ] **10.2** Aba "Simulação": saldo virtual, posição, lucro/prejuízo, curva de capital, lista de operações, métricas, versões do modelo, gráfico de calibração.
- [ ] **10.3** Registro das operações reais (manual) separado da simulação, com lucro/prejuízo em R$ e resumo diário.
- [ ] **10.4** Rótulos e cores distintos para SIMULADO vs REAL.
- [ ] **10.5** Estados de carregamento/erro amigáveis (sem stack trace na tela).
- [ ] **10.6** Aviso visível: "Ferramenta educacional, não é recomendação financeira. Resultados simulados não garantem resultados reais."
- [ ] **10.7** Capturas de tela (ou GIF) para o README.

## ETAPA 11 — TESTES, LINT E CI

- [ ] **11.1** Testes (pytest) para coleta, indicadores, sinais, backtest, carteira virtual, registro de operações, promoção de modelo e risco.
- [ ] **11.2** Lint e formatação (ruff/black) e pre-commit.
- [ ] **11.3** GitHub Actions rodando lint + testes.
- [ ] **11.4** Relatório de cobertura das partes críticas.

## ETAPA 12 — DOCUMENTAÇÃO PARA O GITHUB

- [ ] **12.1** `README.md` profissional em português (+ seção curta em inglês): descrição, problema, screenshots/GIF, funcionalidades, arquitetura (Mermaid), tecnologias, instalação passo a passo (incluindo modo demo), resultados reais do backtest e da simulação, limitações, aviso legal, próximos passos.
- [ ] **12.2** `requirements.txt`/`pyproject.toml`, `.gitignore`, `.env.example`, `LICENSE` (MIT), `Dockerfile` (se fizer sentido).
- [ ] **12.3** Teste de instalação do zero em um ambiente limpo seguindo apenas o README.
- [ ] **12.4** Varredura final por dados sensíveis (chaves, saldos, operações reais, histórico git).
- [ ] **12.5** Sugerir nome do repositório, descrição curta e topics.

## ETAPA 13 — LINKEDIN, CHANGELOG E AUDITORIA FINAL

- [ ] **13.1** `LINKEDIN.md`: 3 versões de post (curta, média, técnica), descrição para "Projetos" e palavras-chave. Somente números reais das etapas anteriores.
- [ ] **13.2** `CHANGELOG.md` com o antes/depois.
- [ ] **13.3** Auditoria final: reler este arquivo inteiro, confirmar que todos os itens das etapas 0 a 12 estão [x] com evidência, rodar tudo do zero e listar pendências, bloqueios e adiados.
- [ ] **13.4** Resumo final: o que foi corrigido, o que melhorou (com números), o que ficou pendente e como rodar o projeto.

---

## Registro de arquivos por etapa

### Etapa 0
- Criados: `PROGRESSO.md`, `.git/` (repositório)
- Alterados: nenhum
- Removidos: nenhum

### Etapa 1
- Criados: `DIAGNOSTICO.md`
- Alterados: `PROGRESSO.md`
- Removidos: nenhum
- Fora do repositório (temporários, no scratchpad da sessão): `overlay_diag.yaml`, `reports/backtest.html`, `reports/backtest.json`
