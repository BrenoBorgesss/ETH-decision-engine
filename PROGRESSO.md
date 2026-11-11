# PROGRESSO — IA_para_Criptos_2.0

Checklist vivo da revisão do projeto. Atualizado a cada item concluído.
Legenda: `[ ]` pendente · `[x]` concluído com evidência · `[BLOQUEADO]` · `[ADIADO]`

**Regra:** uma etapa por vez; só avanço com "aprovado" ou "próxima etapa".
**Ao retomar com contexto limpo:** reler este arquivo inteiro antes de continuar.

| Etapa | Nome | Estado |
|---|---|---|
| 0 | Preparação | concluída e aprovada (2026-11-10) |
| 1 | Diagnóstico | concluída e aprovada (2026-11-10) |
| 2 | Segurança e configuração | concluída e aprovada (2026-11-11); 1 item extra adiado (biblioteca do gráfico) |
| 3 | Estrutura e qualidade do código | concluída — aguardando aprovação (1 item adiado) |
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

Decisões do usuário em 2026-11-10 ("Aprovado" às recomendações D1 a D4): manter os nomes das pastas; criar modelo de regressão logística como desafiador (Etapas 6/8); seguir sem depender da chave; remover `ANTHROPIC_API_KEY`.
Backup antes da etapa: tag git `backup-antes-etapa-2` (aponta para `d8aaa3f`).

- [x] **2.1** Remover/mover segredos para variáveis de ambiente; criar `.env.example` e `.gitignore`.
  - Já existia: segredos só no `.env`, lidos apenas em `config.py` (`load_secrets`); `.env.example` e `.gitignore` presentes. Confirmado: `grep os.environ|getenv` fora de `config.py` → 1 ocorrência (`alerts/notify.py`, tratada abaixo).
  - **Bug encontrado e corrigido:** a regra `data/` do `.gitignore` também ignorava o pacote de código `src/cryptoagent/data/` (7 arquivos: `rest.py`, `history.py`, `websocket.py`, `health.py`, `market_store.py`, `models.py`, `__init__.py`), que ficou FORA do commit inicial. O repositório público sairia sem o módulo de coleta. Regras trocadas por `/data/` e `/logs/` (só na raiz). Evidência: `git check-ignore` antes → `.gitignore:14:data/ src/cryptoagent/data/rest.py`; depois → não ignorado, e `.env`, `data/agent.sqlite`, `data/history/.../1m.parquet`, `logs/agente.log` continuam ignorados. Commit `045273f` (arquivos no estado original).
  - `ANTHROPIC_API_KEY` removida de `config.py` e do `.env.example` (nada a usava). Evidência: `grep -i anthropic` em `src`, `config`, `.env.example` → vazio. Commit `83a4d05`.
  - Notificação do Windows: o processo filho (PowerShell) herdava todo o ambiente, incluindo as chaves carregadas do `.env`. Agora não herda `BINANCE_API_KEY`/`BINANCE_API_SECRET`. Teste `test_notificacao_do_windows_nao_herda_as_chaves`. Commit `0fb9491`.
  - Novos testes: `test_segredos_sao_so_os_da_binance`, `test_sem_env_o_agente_roda_sem_chave`.
  - Nenhuma chave vazou no repositório; nada a revogar. (Sugestão, não obrigação: apagar a linha `ANTHROPIC_API_KEY` do seu `.env`.)
- [x] **2.2** Garantir modo somente leitura da Binance e a trava de execução real desativada por padrão.
  - Confirmado por execução: `pytest tests/test_readonly_guard.py tests/test_rest_client.py tests/test_permissions.py` → `30 passed`. Buscas em `src`: métodos de escrita HTTP fora das rotas locais = 0; endpoints de ordem/saque/transferência = 0. `ALLOWED_PATHS` tem 17 caminhos, todos de leitura (`data/rest.py:41-65`).
  - Não existe código de execução real, então não há o que desligar; registrado no README (seção Segurança) junto com a regra para o futuro (desligado por padrão, flag explícita, aviso).
  - Reforço: o painel recusa (HTTP 400) qualquer chamada com cabeçalho `Host` diferente de `127.0.0.1`/`localhost` (proteção contra DNS rebinding; antes `/api/estado` respondia a qualquer Host). Teste manual com servidor uvicorn real na porta 8799: Host local → 200; `Host: evil.example.com` → 400 em `/`, `/api/estado`, `/static/app.js` e `POST /api/sessao`; POST local sem token → 403. Testes automáticos: `test_host_de_fora_e_recusado_em_todas_as_rotas` (4 casos), `test_host_local_e_aceito` (3 casos). Commit `0fb9491`.
- [x] **2.3** Criar configuração centralizada (YAML/.env) e eliminar números mágicos.
  - Já existia: `config/config.yaml` validado por pydantic, com erro em campo desconhecido.
  - Movidos para o config, com os mesmos valores: `binance.candles_por_consulta` e `candles_por_consulta_padrao` (era `KLINE_LIMITS` em `engine/live.py` e `limit=1000` em `dashboard/service.py`); `historico.timeout_download_s`, `tentativas_download`, `espera_entre_tentativas_s` (eram 60, 3 e 1,5 em `cli.py` e `data/history.py`); `estrategia.stop_posicao_sem_sinal_atr_5m` (era `POSITION_STOP_ATR_5M = 2.0`), `adx_vies_baixa_1h` (era `>= 20` em `engine/rules.py`), `penalidade_por_aviso_dados` (era `0.05` em `engine/decision.py`); `painel.buffer_candles` e `min_candles_ws` (eram 1100 e 300); `logs.tamanho_max_mb`, `arquivos_antigos`, `formato`.
  - Prova de comportamento preservado: `backtest --rapido --pares ETHBRL` antes e depois gerou JSON idêntico byte a byte (SHA-256 `c348cc24…cf544` nos dois). Teste `test_constantes_que_viraram_config_mantem_os_valores_antigos` e `test_validacao_dos_campos_novos`.
  - Testes manuais: sobreposição com `logs.formato: xml` → `Erro no arquivo de configuração ... Input should be 'texto' or 'json'`, exit 1; campo digitado errado → `Extra inputs are not permitted`, exit 1; `historico verificar` com as variáveis de chave vazias → exit 0.
  - Commit `83a4d05`.
  - **[ADIADO] números que continuam no código**, com motivo:
    - `backtest/metrics.py:102` (taxa de 0,1% no comprar e segurar): mudar altera resultados; fica para a Etapa 6 (problema B5).
    - `engine/rules.py` (confianças fixas das saídas: 100, 95, 90, 80, 85, 75): são rótulos de pontuação, não parâmetros; decidir na Etapa 6 junto com a calibração.
    - `data/history.py` `max_requests=2000` e `data/rest.py` backoff (0,5 s × 2^n, teto 30 s): revisar na Etapa 4.1, que reescreve a política de repetição.
    - Arquivos ainda não lidos (lista no fim do `DIAGNOSTICO.md`) podem ter outros; cada um será revisto na etapa que o tocar.
- [x] **2.4** Adicionar logging estruturado no lugar de print.
  - Não havia `print()` usado como log: as 123 ocorrências são `console.print` (interface de terminal da biblioteca rich). O log já era em arquivo rotativo, UTC, com filtro de segredos.
  - Novo: `logs.formato: json` grava um objeto JSON por linha (`ts`, `nivel`, `logger`, `msg`, `erro`, e campos passados em `extra={...}`), com segredos mascarados em todos os campos. Padrão continua `texto`.
  - Teste manual lendo o config real + sobreposição: `{"ts": "2026-11-11T03:18:30Z", "nivel": "INFO", "logger": "cryptoagent.teste_manual", "msg": "config carregado: formato=json nivel=INFO", "par": "ETHBRL"}` e, na linha seguinte, `"msg": "tentativa de vazar a chave *** e a assinatura signature=***"`.
  - 6 testes novos em `tests/test_logging_setup.py`. Commit `cf263bb`.
- [x] **Extra** `.gitattributes` (fim de linha LF; `.ps1` em CRLF). Commit `045273f`.
- [ADIADO] **Extra (S3 do diagnóstico)** Biblioteca do gráfico carregada de CDN sem verificação de integridade (`index.html:8`). Corrigir exige baixar o arquivo da biblioteca (para calcular o hash ou servir localmente). Aguardando autorização do usuário para o download.

### Verificação final da Etapa 2
- `python -m pytest -q` → `231 passed, 1 warning in 18.93s` (eram 213; +18 testes novos).
- Suíte também rodada só com o commit `83a4d05` aplicado (demais mudanças em stash): `217 passed`.
- `git status --short` → vazio.

### Problemas durante a Etapa 2
- Dois scripts de edição enviados por heredoc falharam por causa de aspas simples no conteúdo; nada foi aplicado pela metade (passei a gravar os scripts em arquivo).
- `history.py` foi editado antes de eu perceber que ele nunca tinha sido versionado. Restaurei o conteúdo original, fiz o commit `045273f` e só então reapliquei a mudança, para o histórico mostrar o diff verdadeiro.

## ETAPA 3 — ESTRUTURA E QUALIDADE DO CÓDIGO

Backup antes da etapa: tag git `backup-antes-etapa-3` (aponta para `bf93869`).
Ferramentas de análise (ruff 0.17.4, vulture 2.16, pylint) instaladas num ambiente virtual separado, fora do projeto; o `.venv` do projeto não foi alterado.

- [x] **Pré-requisito** Congelar os custos do backtest para ter um baseline estável (problema M3 do diagnóstico).
  - `backtest --custos-arquivo ARQUIVO.json`: se o arquivo existe, usa os custos dele e não consulta a conta nem o banco de custos; se não existe, grava os custos da execução. Novas funções `save_schedules`/`load_schedules` e `CostSchedule.to_dict/from_dict` em `backtest/costs.py`.
  - Demonstração real: 1ª execução (grava) e 2ª (lê) geram o mesmo JSON, SHA-256 `c348cc24…cf544`, igual ao baseline da Etapa 2. Com a taxa do config trocada para 0,2% e o arquivo congelado: mesmo hash. Com a mesma taxa trocada SEM o arquivo: 1 operação, +0,53% (prova de que o congelamento é o que segura o resultado).
  - Erro meu corrigido no caminho: a primeira versão devolvia as horas em ordem de texto ("0", "1", "10"...), o que mudava os bytes do relatório sem mudar os números. Corrigido para ordem numérica e coberto por teste.
  - 2 testes novos em `tests/test_backtest.py`. Commit `6e01d2c`.
- [x] **3.1** Reorganizar em módulos, sem quebrar o que já funciona.
  - Decisão D1 (aprovada): os nomes `engine/`, `paper/`, `indicators/` foram mantidos em vez de `models/`, `simulation/`, `features/`. `learning/` será criado na Etapa 8.
  - `cli.py` (1.330 linhas) virou o pacote `cli/` com 15 módulos (maior: `dados.py`, 185 linhas). Divisão por script: as 41 definições foram movidas sem alterar o corpo. Evidência: comparação de AST → `definicoes no original: 41 | no pacote: 41`, `faltando: []`, `sobrando: []`; única diferença é a ordem de dois `import` locais em `cmd_panel`. O ponto de entrada `cryptoagent.cli:main` não mudou.
  - `--help` de todos os 37 comandos/subcomandos idêntico ao de antes (295 linhas, `cmp` sem diferença) no momento do commit. Commit `eb2e50f`.
  - Ajuste em teste existente: `tests/test_readonly_guard.py` passou a ler todos os arquivos de `cli/` (antes lia `cli.py`); a verificação é a mesma.
- [x] **3.2** Adicionar type hints e docstrings.
  - Type hints: 34 funções tinham parâmetro ou retorno sem anotação; agora 0 (script AST: `sem_retorno: 0, sem_tipo_arg: 0` em 458 funções). Commit `9c6ac1d`.
  - Docstrings: 293 inseridas em funções, classes e métodos públicos, a partir de um mapa escrito depois de ler o código de cada item. O script de inserção confere que o AST de cada arquivo, sem as docstrings, é idêntico ao anterior. Commit `dcb7625`.
  - Sem docstring, de propósito: 6 métodos de uma linha dos `Protocol` (`ServiceLike`, `KlinesSource`) e 134 funções privadas ou aninhadas (prefixo `_`, validadores do pydantic, funções internas). A regra de lint da Etapa 11 dirá se alguma delas passa a ser exigida.
- [x] **3.3** Remover código morto e duplicação.
  - vulture (confiança ≥ 60) apontou 49 itens; cada um foi conferido por busca. A maioria é falso positivo (validadores do pydantic, campos de dataclass, rotas do FastAPI). Removidos os confirmados sem nenhum uso: `engine/journal.last_signal`, `SymbolAnalysis.hour_row`, campo `RiskView.quote_to_brl`.
  - pylint `duplicate-code` (≥ 6 linhas iguais): nenhum bloco duplicado, antes e depois.
  - Duplicação pequena removida: `REGIME_PT` do terminal agora deriva do dicionário do motor de decisão.
  - 11 lambdas atribuídas a nome viraram funções (inclui o problema B6); `timezone.utc` → `datetime.UTC`.
  - Mantidos de propósito: campos informativos nunca lidos (`Check.limit_s`, `Stamped.exchange_ms`, `CandleBuffer.last_update_ms/last_source`, `SimStats.decision_bars`, `AccountStatus.trades_synced`, `Attribution.op_qty/ignored_qty`, `FillEstimate.best_price`) e as constantes `REGIMES` e `CLASSES`: remover mexeria em construtores posicionais sem ganho. Commit `1919711`.
  - **Achado novo (B7):** `conta.saldo_max_s` existe no config com o comentário "leitura de saldo mais velha que isso = ESPERAR", mas nenhum código lê esse campo. Hoje o saldo é relido a cada análise, então a idade é sempre próxima de zero; ainda assim é uma proteção documentada e não implementada. Decidir na Etapa 9 (implementar ou remover).
- [x] **3.4** Confirmar que o projeto continua rodando igual ao baseline (comparar saídas).
  - Testes: `233 passed, 1 warning in 17.38s` (eram 231; +2 dos custos congelados).
  - Backtest com custos congelados depois de todas as mudanças: SHA-256 `c348cc24a1f7e7aca7a610c8e9c0f8d820ca6ffaa9c5992a6b4c77de959cf544`, idêntico ao baseline da Etapa 2 (13 operações, −3,67%).
  - `--help`: única diferença em relação ao baseline é a opção nova `--custos-arquivo` do comando `backtest`.
  - Importação de `cryptoagent.cli`: 1,06 s (era 0,99 s; diferença dentro da variação entre execuções, não medida com repetição).
- [ADIADO] **Quebrar as 4 funções mais longas** (`simulate` 198 linhas, `analyze_live`, `AccountMonitor.refresh`, `decide`): continuam 8 funções com mais de 80 linhas. Proposta: quebrar cada uma na etapa que já vai reescrevê-la (`analyze_live` na 5, `simulate` e `decide` na 6, `refresh` na 9), para não refatorar duas vezes. Aguardando sua concordância.

### Métricas de qualidade, antes × depois (script AST)
| Métrica | Etapa 1 | Etapa 3 |
|---|---|---|
| Maior arquivo | `cli.py`, 1.328 linhas | `config.py`, 534 linhas |
| Funções sem anotação de retorno | 13 | 0 |
| Funções com parâmetro sem tipo | 11 | 0 |
| Funções sem docstring | 325 de 440 | 134 de 458 |
| Funções com mais de 80 linhas | 8 | 8 |
| Blocos duplicados (≥ 6 linhas) | não medido | 0 |

### Problemas durante a Etapa 3
- Um `sed` meu quebrou uma linha de `tests/test_readonly_guard.py`; o teste acusou na hora e corrigi antes do commit.
- Tentei remover dois atributos de `CandleBuffer` e desisti: deixaria parâmetros sem uso em métodos públicos. Restaurei o arquivo com `git checkout`.

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

### Etapa 2
- Criados: `.gitattributes`, `tests/test_logging_setup.py`
- Passaram a ser versionados (já existiam no disco, escondidos pelo `.gitignore`): `src/cryptoagent/data/__init__.py`, `health.py`, `history.py`, `market_store.py`, `models.py`, `rest.py`, `websocket.py`
- Alterados: `.gitignore`, `.env.example`, `README.md`, `config/config.yaml`, `src/cryptoagent/config.py`, `src/cryptoagent/cli.py`, `src/cryptoagent/logging_setup.py`, `src/cryptoagent/alerts/notify.py`, `src/cryptoagent/dashboard/server.py`, `src/cryptoagent/dashboard/service.py`, `src/cryptoagent/data/history.py`, `src/cryptoagent/engine/decision.py`, `src/cryptoagent/engine/live.py`, `src/cryptoagent/engine/rules.py`, `tests/test_config_and_store.py`, `tests/test_dashboard.py`, `PROGRESSO.md`
- Removidos: nenhum
- Não tocados: `.env`, `data/`, `logs/`

### Etapa 3
- Criados: `src/cryptoagent/cli/` com `__init__.py`, `_shared.py`, `app.py`, `backtest.py`, `conta.py`, `custos.py`, `dados.py`, `decisao.py`, `diario.py`, `historico.py`, `indicadores.py`, `painel.py`, `paper.py`, `sazonalidade.py`, `sessao.py`
- Removidos: `src/cryptoagent/cli.py` (conteúdo movido para o pacote acima)
- Alterados: 54 arquivos de `src/cryptoagent` (docstrings, anotações e limpeza; lista exata em `git diff --name-status backup-antes-etapa-3 HEAD`, que mostra 15 criados, 1 removido e 57 alterados no total), `tests/test_backtest.py`, `tests/test_readonly_guard.py`, `PROGRESSO.md`
- Não tocados: `.env`, `data/`, `logs/`, `config/`, `README.md`
