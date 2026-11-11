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

Backup antes da etapa: tag git `backup-antes-etapa-4` (aponta para `7e663fe`).

- [x] **4.1** Módulo central de coleta com timeout, retry com backoff exponencial e tratamento de rate limit.
  - Situação encontrada: o cliente REST (`data/rest.py`) já tinha timeout, repetição com espera crescente, 429/418 e limite de peso. O download dos arquivos de histórico (`data/history.py`) tinha política própria e mais fraca: espera linear, sem tratar 429 nem 418, e repetia até erro 4xx (problema C4 do diagnóstico).
  - Feito: `data/retry.py` com uma `RetryPolicy` única (tentativas, espera inicial × 2^tentativa até um teto, sorteio de 0,5 a 1,5×, `Retry-After` com valor padrão e teto), usada pelos dois. O download agora: repete falha de rede, timeout e 5xx; no 429 espera o `Retry-After`; no 418 e em 4xx para na hora; 403/404 continua significando "arquivo não existe".
  - A atualização incremental pela REST passou a gravar o que já chegou quando a rede cai no meio (antes perdia todas as páginas já baixadas).
  - Números antes fixos no código foram para o config: `binance.espera_inicial_s`, `espera_max_s`, `espera_429_padrao_s`, `espera_429_max_s`; `historico.espera_max_s`, `max_consultas_rest`. Os valores padrão são os mesmos de antes (0,5 s, 30 s, 60 s, 2000). Mudou de propósito: a espera do download era 1,5 s × tentativa e agora é 1,5 s × 2^tentativa; o 429 passa a ter teto de 300 s.
  - Evidência (falhas de rede simuladas): 8 testes novos em `tests/test_history.py` — sequência falha de conexão → timeout → 503 → sucesso com esperas `[1.0, 2.0, 4.0]`; desistência depois de 3 tentativas com esperas `[1.0, 2.0]`; 429 espera `12.0`; 418 e 400 com 1 chamada e nenhuma espera; 404 devolve vazio sem repetir. Os 4 testes equivalentes do cliente REST (`tests/test_rest_client.py`) continuam passando sem alteração. Commit `fc3c5fe`.
- [x] **4.2** Validação dos dados (nulos, duplicados, timestamps fora de ordem, outliers, lacunas de candles).
  - Situação encontrada: só havia remoção de duplicados, ordenação e busca de lacunas (problema C2).
  - Feito: `data/validation.py` (`clean_klines`, `inspect_klines`, `find_outliers`, `QualityReport`). Verifica valor ausente/não finito, duplicados, ordem, horário fora da grade do intervalo, fechamento incoerente com a abertura, preço ≤ 0, máxima < mínima, abertura/fechamento fora da faixa, volume negativo, candle do futuro, outliers e lacunas. Linhas inválidas são removidas; duplicados e ordem são corrigidos; outliers e lacunas são só relatados (podem ser reais).
  - Onde entra: download e atualização do histórico (descarta e registra no log); `historico verificar` (colunas Inválidos e Outliers; código de saída 2 se houver linha com problema); análise ao vivo (candle inválido vira verificação crítica → ESPERAR); backtest (recusa histórico com candle inválido).
  - Critério de outlier: retorno a mais de `historico.outlier_desvios` (30) desvios robustos da mediana. Erro meu corrigido antes do commit: a primeira versão usava o desvio absoluto mediano, que fica perto de zero no USDTBRL (44,9% dos retornos de 1m são zero) e marcava 4.575 candles de 5m como outlier. Troquei pelo percentil 90 dos desvios ÷ 1,645; passou a marcar 7.
  - Evidência nos dados reais (14 séries, 2,34 milhões de candles): 0 inválidos, 0 duplicados, 0 fora de ordem, 0 lacunas; outliers: ETHUSDT 1m 16, 5m 2; ETHBRL 1m 10; USDTBRL 1m 61, 5m 7, 15m 3; demais 0. A tabela limpa é idêntica à original em todas (`igual=True`), então o backtest não mudou: SHA-256 `c348cc24…cf544`, igual ao baseline. Tempo da validação: 0,44 s na maior série (628 mil candles).
  - 15 testes novos em `tests/test_validation.py`. Commit `f9da9a7`.
- [x] **4.3** Cache local (SQLite ou parquet) com coleta incremental.
  - Situação encontrada: já existia (Parquet por par/intervalo + manifesto dos arquivos já baixados + complemento pela REST a partir do último candle). Faltavam a prova e o aviso de desalinhamento (problema C3).
  - Feito: gravação parcial em caso de queda (item 4.1); `historico verificar` ganhou a coluna Atraso (candles que faltam para a série alcançar a mais recente) e o aviso "As séries terminam em momentos diferentes".
  - Demonstração real (pasta temporária, sem tocar em `data/history`): `historico baixar --meses 1 --simbolos ETHBRL` com 1h e 15m. 1ª execução: 11 arquivos novos por série, 3 e 15 candles via REST, 10,2 s. 2ª execução: 0 arquivos novos, 11 "já tinha", 0 via REST, 1,8 s, mesmas 987 e 3.951 linhas.
  - Teste novo: queda de rede depois de 2 páginas → as 2.000 linhas ficam gravadas; a execução seguinte começa exatamente no candle 2.010 e a terceira não traz nada.
  - Achado real mostrado pelo comando: no seu histórico, ETHBRL 1m vai até 11/11 03:00 UTC, os 15m até 10/11 21:45 e as demais séries pararam em 22/10 (até 28.660 candles de atraso). Não corrigi (seria baixar dados para a sua pasta); basta rodar `cryptoagent historico baixar`.
- [x] **4.4** Valores em R$ (par ETH/BRL ou conversão confiável) e timezone consistente.
  - Auditoria: os preços vêm do próprio par ETHBRL (cotação direta em R$). Quando a análise é feita no ETHUSDT, a conversão usa a razão entre os preços atuais dos dois pares, e os dois passam pela verificação de idade (`engine/live.py`). Todo horário é guardado em ms UTC (int64) e só vira horário de Brasília na exibição (`timeutils.py`, com zoneinfo).
  - Evidência nos dados reais: ETHBRL × (ETHUSDT × USDTBRL) em 599.361 candles de 1m: diferença mediana 0,0196%, percentil 99 0,1247%, máxima 2,1632%; em 9.989 candles de 1h: mediana 0,0177%, máxima 0,2942%. Todas as séries de 1h começam em hora cheia UTC.
  - Feito: a validação (4.2) reprova horário fora da grade UTC; teste novo `test_codigo_nao_usa_horario_sem_fuso` varre `src/` atrás de `datetime.now()` sem fuso, `utcnow`, `date.today`, `fromtimestamp` sem fuso (0 ocorrências); tabelas de `historico` passaram a usar o formato brasileiro de número (628.021 em vez de 628,021). Commit `2e6eae6`.
- [x] **4.5** Modo offline/demonstração com dados históricos de exemplo (sem dados pessoais).
  - Feito: opção global `--demo`. Usa `config/demo.yaml` (sobreposição), não lê o `.env`, não usa a rede e grava só em `data/demo/`. Comandos disponíveis: `historico verificar`, `indicadores validar`, `backtest`. Os outros respondem com uma mensagem clara e código 2.
  - Amostra em `exemplos/historico/ETHBRL`: 1m, 5m, 15m e 1h copiados do histórico público (25/06 a 01/10/2026 UTC); 4h montado somando o 1h. Total 7,79 MB. Gerada e validada por `scripts/gerar_amostra_demo.py`. Só tem colunas de mercado (teste confere).
  - Prova da montagem do 4h: no ETHUSDT, que tem 1h e 4h publicados, os 2.497 candles montados batem com os reais (abertura, máxima, mínima, fechamento, horário e nº de negócios iguais em 2.497/2.497; volumes com diferença relativa máxima de 2,22e-16).
  - `exemplos/custos_demo.json`: taxa 0,1% e spread 0,04% do config ("do config (sem leitura da conta)"), nenhuma medição da sua conta.
  - Execução real: `cryptoagent --demo backtest --rapido` em 7,6 s → fora da amostra 31/08 a 30/09/2026: 10 operações, −2,79%, drawdown −2,79%, acerto 20,0%, Sharpe −5,56; comprar e segurar +8,50%. É uma demonstração de que roda, não uma avaliação (1 mês de teste).
  - 6 testes em `tests/test_demo.py`, com a rede (`httpx.AsyncClient.send`) e a leitura de chaves (`load_secrets`) trocadas por funções que falham se chamadas. Commit `cd19439`.
  - Decisão: no demo a análise é feita no próprio ETHBRL (no uso normal é no ETHUSDT), para a amostra caber no repositório; incluir o ETHUSDT de 1m dobraria o tamanho.
- [ADIADO] **Painel e análise ao vivo no modo demo.** O demo desta etapa cobre os comandos que leem o histórico. Mostrar o painel sem internet exige reproduzir candles gravados como se fossem ao vivo, que é o modo replay da Etapa 7.5; a tela é da Etapa 10. Proponho entregar lá.
- [ADIADO] **C5 do diagnóstico** (cada atualização regrava o Parquet inteiro, 31 MB no 1m). É desempenho: medir e decidir na Etapa 5.
- **Achado novo (C7):** no ETHBRL, 27.883 de 142.560 candles de 1m da amostra têm volume zero (par pouco negociado). Nesses trechos alguns indicadores ficam indefinidos: 56 NaN no 1m (VWAP 26, RVOL 17, %B 13) e 1 no 5m, depois do aquecimento. O backtest não é afetado hoje (o 1m não usa indicadores e os do 5m exigidos estão completos), e no uso normal a análise é no ETHUSDT. Tratar na Etapa 6. Acrescentei uma frase explicando isso no rodapé de `indicadores validar`.

### Verificação final da Etapa 4
- `python -m pytest -q` → `263 passed, 1 warning in 24.21s` (eram 233; +30 testes).
- Backtest de referência com custos congelados depois de tudo: SHA-256 `c348cc24a1f7e7aca7a610c8e9c0f8d820ca6ffaa9c5992a6b4c77de959cf544`, idêntico ao baseline (13 operações, −3,67%).
- `--help`: única diferença em relação à Etapa 3 é a opção nova `--demo`.
- `git status --short` → vazio.

### Problemas durante a Etapa 4
- Critério de outlier errado na primeira versão (ver 4.2); visto ao rodar nos dados reais e corrigido antes do commit.
- A primeira amostra do demo terminava em 30/09 e o backtest recusou ("histórico curto demais"): o walk-forward só aceita um mês de teste se existir candle do mês seguinte. Estendi a amostra em 1 dia.
- Um teste do demo falhou porque a tabela era cortada no terminal estreito do pytest; fixei a largura no teste.
- Um script de edição enviado por heredoc falhou de novo por causa de aspas; nada foi aplicado pela metade.

## ETAPA 5 — PERFORMANCE E EFICIÊNCIA

Backup antes da etapa: tag git `backup-antes-etapa-5` (aponta para `a9d4ec2`).

- [x] **5.1** Medir o baseline (tempo de inicialização, coleta, cálculo de indicadores, memória) com profiling real.
  - `scripts/medir_desempenho.py`: mede inicialização, leitura do histórico, validação, indicadores, backtest, gravação e análise ao vivo. Cada seção roda em processo próprio (o pico de memória de uma não contamina a outra) e cada tempo é a mediana de várias repetições. Grava um JSON. Roda no histórico completo ou, com `--demo`, na amostra do repositório.
  - Baseline no histórico completo (coluna "Antes" da tabela abaixo). O que ele mostrou: 92% do tempo do backtest estava na simulação (31,2 s de 33,8 s); ler, validar e gravar o histórico já eram rápidos; iniciar o programa custava 1,2 s mesmo para um `--help`.
  - cProfile de uma simulação: 178.535 chamadas de `frame.iloc[-1]` do pandas dentro da função de decisão somavam cerca de 12 s (montar uma linha de tabela com colunas de tipos diferentes custa dezenas de microssegundos). Depois vinham os recortes de janela e os `.min()` de poucas linhas.
  - `python -X importtime`: dos 1,12 s de importação, 0,54 s eram do pandas e o resto de httpx, pydantic e dos módulos de comando, todos carregados antes de saber qual comando rodar.
  - Análise ao vivo: medida com uma Binance simulada (`tests/fake_binance.py`, candles da amostra) e a carteira virtual, porque a chave real está recusada e eu não leio a sua conta. 0,238 s de processamento por análise; com 50 ms de latência simulada por consulta, 0,537 s (5 esperas em sequência).
- [x] **5.2** Corrigir gargalos (vetorização, cache de indicadores, evitar recálculo, coleta assíncrona/em segundo plano).
  - **Backtest (leitura por arrays).** `MarketView.last()` e `MarketView.tail()` leem a última linha e as últimas N posições de uma coluna. No backtest, o simulador entrega leitores que vão direto nos arrays do numpy (`ArrayRow`); ao vivo continuam vindo do DataFrame. O motor de decisão (`decision.py`, `rules.py`, `signals.py`) passou a usar esses dois métodos. O custo de ida e volta por hora é calculado uma vez por simulação (antes, 110 mil vezes). Commits `19e3f4d` e `46f23e4`.
  - **Inicialização (importação preguiçosa).** Commit `c6259cf`. Os comandos são registrados por nome ("módulo:função") e importados só na hora de rodar. `intervals.py` e `cli/_console.py` são módulos leves para o `--help` não puxar pandas. Conferido: depois de `import cryptoagent.cli`, nenhum de pandas, numpy, httpx, pydantic ou fastapi está carregado.
  - **Análise ao vivo (cache de indicadores).** `indicators/cache.py`: guarda o resultado por (par, timeframe) com uma impressão digital dos candles fechados (quantidade, primeiro e último horário, soma de fechamento e de volume) e dos parâmetros; devolve cópia. Entre duas análises os candles de 15m, 1h e 4h quase sempre são os mesmos.
  - **Análise ao vivo (coleta em paralelo).** Antes: relógio → candles → preço → preço do par de operação → livro → derivativos, um depois do outro. Agora: relógio → (candles + derivativos em paralelo) → (preços + livro em paralelo). Os preços continuam sendo buscados por último, para chegarem frescos à decisão.
  - **`analyze_live` dividida** (pendência da Etapa 3; commit `7d24cab`, junto com o cache e a coleta em paralelo): 170 → 108 linhas, com `_fetch_candles`, `_fetch_funding`, `_prepare_frames`, `_fetch_quotes`, `_structure_levels` e `_season_context`. Os dois últimos são blocos movidos sem alteração.
  - Antes de mexer na análise ao vivo, criei `tests/test_live.py` com uma "foto" do resultado num instante da amostra (ação, confiança, regime, preço, notas dos sinais, níveis e lista de verificações). A foto é a mesma antes e depois.
  - Não mexi: o cálculo dos indicadores em si. O profiling de 1.000 candles não mostrou um ponto dominante (são dezenas de operações pequenas do pandas), e reescrever em numpy arriscaria mudar valores. O cache resolve o caso que importa (ao vivo).
- [x] **5.3** Medir de novo e registrar a tabela antes/depois com números reais.
  - Mesmo script, mesma máquina, histórico completo (ETHUSDT 599.455 candles de 1m; backtest do ETHBRL com custos do config):

| Medida | Antes | Depois | Variação |
|---|---|---|---|
| Python vazio (referência) | 0,04 s | 0,04 s | +8% |
| Importar o programa | 1,19 s | 0,24 s | -80% |
| cryptoagent --version | 1,23 s | 0,27 s | -78% |
| cryptoagent --help | 1,26 s | 0,27 s | -79% |
| Ler 1m do disco (599 mil candles) | 0,032 s | 0,026 s | -19% |
| Validar 1m (599 mil candles) | 0,43 s | 0,43 s | -0% |
| Gravar 1m inteiro (36,7 MB) | 0,23 s | 0,25 s | +5% |
| Indicadores no histórico, 1m | 1,606 s | 1,559 s | -3% |
| Indicadores no histórico, 5m | 0,317 s | 0,321 s | +1% |
| Indicadores no histórico, 15m | 0,123 s | 0,127 s | +3% |
| Indicadores no histórico, 1h | 0,052 s | 0,050 s | -4% |
| Indicadores no histórico, 4h | 0,035 s | 0,037 s | +5% |
| Indicadores ao vivo, 5 timeframes × 1000 candles | 0,153 s | 0,154 s | +0% |
| Backtest rápido: carregar e preparar | 2,64 s | 2,62 s | -1% |
| Backtest rápido: simular | 31,20 s | 7,87 s | -75% |
| Backtest rápido: total | 33,83 s | 10,49 s | -69% |
| Backtest: pico de memória | 651 MB | 655 MB | +1% |
| Análise ao vivo, 1ª da sessão (sem latência) | 0,253 s | 0,238 s | -6% |
| Análise ao vivo, seguintes (sem latência) | 0,238 s | 0,147 s | -38% |
| Análise ao vivo, seguintes (50 ms por consulta) | 0,537 s | 0,327 s | -39% |
| Análise ao vivo: pico de memória | 241 MB | 248 MB | +3% |

  - O resultado do backtest é o mesmo antes e depois: 12 operações, −3,2106469980240693% nas duas medições (custos do config), e SHA-256 `c348cc24…cf544` no backtest de referência com custos congelados (13 operações, −3,67%).
  - Variações de até ±8% nas linhas que não foram alteradas são ruído entre execuções.
  - Memória: o pico do backtest é 651 MB (carregar o histórico de dois pares em 1m com ~60 colunas de indicadores). Não foi alvo desta etapa e não mudou.
- [x] **C5 do diagnóstico** (regravar o Parquet de 1m inteiro a cada atualização): medido, 0,23 s para 36,7 MB. Não é gargalo; mantive o formato atual, mais simples. Encerrado sem mudança.
- [ADIADO] **`analyze_live` ainda tem 108 linhas** (a meta informal era ficar abaixo de 80). O que sobra são os dois ramos finais, carteira virtual e conta real; o da conta real não tem teste e será reescrito na Etapa 9, onde termino a divisão.
- [x] **Backtest completo (grade + ablação)** (resolvido na Etapa 6): medido numa cópia do código de antes da Etapa 5 (`git worktree` na tag `backup-antes-etapa-5`) e no código atual, mesmos dados e custos congelados: **250 s → 67 s** (−73%), com as mesmas linhas de resultado (padrão 13 operações −3,67%; walk-forward 13 operações −4,21%).

### Verificação final da Etapa 5
- `python -m pytest -q` → `270 passed, 1 warning in 12.09s` (eram 263; +7 testes em `tests/test_live.py`). A suíte ficou mais rápida que na Etapa 4 (24 s) por causa do backtest.
- Backtest de referência: SHA-256 `c348cc24a1f7e7aca7a610c8e9c0f8d820ca6ffaa9c5992a6b4c77de959cf544`, idêntico ao baseline, conferido depois de cada uma das três mudanças no backtest e no fim.
- `--help` dos 37 comandos e subcomandos: idêntico ao da Etapa 4 (`diff` vazio).
- `git status --short` → vazio.

### Problemas durante a Etapa 5
- Um `sed` para encurtar caminhos na saída do profiling falhou (barra invertida do Windows); troquei por `awk`. Nenhum arquivo do projeto foi afetado.
- Achado positivo ao montar o teste: nos instantes em que o backtest da amostra compra, a análise ao vivo simulada também decide COMPRAR (5 de 6 instantes conferidos; no sexto, ESPERAR por risco/retorno; não investiguei a causa, a hipótese é o preço "de agora" da simulação, que é o fechamento do minuto seguinte). É um indício de coerência entre os dois caminhos, não uma prova; a auditoria é da Etapa 6.

## ETAPA 6 — MODELO, BACKTEST E CONFIABILIDADE

Backup antes da etapa: tag git `backup-antes-etapa-6` (aponta para `2f767af`).
Relatório completo da auditoria: `docs/AUDITORIA_BACKTEST.md`. Nova dependência: `scikit-learn` (1.9.1 instalada; `>=1.7` no `pyproject.toml`), aprovada na decisão D2.

- [x] **6.1** Auditar vazamento de dados, look-ahead bias e overfitting; documentar o que foi achado.
  - `tests/test_sem_futuro.py` (6 testes): indicadores calculados com o histórico inteiro x cortado (35%, 60%, 85%) e x futuro ALTERADO (preços com choque de ~2% e volumes invertidos depois do corte). Linhas anteriores ao corte idênticas com tolerância zero em 5m, 15m, 1h e 4h. Sensibilidade do método provada: um indicador que usa o próximo fechamento é pego.
  - Simulação completa no histórico real (análise por horário ligada), cortada em 15/03, 10/06 e 05/09/2026: 20, 29 e 38 operações encerradas antes do corte, idênticas (entrada, saída, resultado, motivo e confiança).
  - Backtest x ao vivo: indicadores com os últimos 1.000 candles x histórico inteiro, 25 instantes por timeframe: maior diferença relativa 3,0e-5 (EMA 200 do 4h); regime e estrutura iguais em 25 de 25.
  - **B2:** nenhuma regra de saída usa suportes/resistências; teste novo em `tests/test_engine.py` garante.
  - **B3 explicado:** a trava de risco/retorno (≥ 1 depois dos custos) implica distância ≥ risco + 2 × custo; dos 557 sinais que passam nela, o menor alvo estava a 2,56× o custo. As variantes de distância 1×, 1,5× e 2,5× não testam nada. Não mudei o código; está documentado.
  - **Vazamento leve conhecido (M6):** a tabela de spread (medida de 22/10 a 11/11) é aplicada a maio–setembro. Medido: 13 operações −3,67% com ela x 12 operações −3,21% com o spread padrão. Conclusão igual.
  - **Overfitting:** parâmetros padrão escolhidos à mão antes desta revisão (não sei se olhando estes meses); 12 variantes no mesmo relatório; 13 operações com intervalo de 95% da média de −0,626% a +0,066%.
- [x] **6.2** Backtest com validação walk-forward (nunca embaralhar séries temporais), com taxas e slippage realistas.
  - Confirmado: janelas por mês-calendário, teste sempre depois do treino (`make_folds`), sem embaralhar; taxa, meio spread por hora, slippage (0,01% a mercado, 0,05% no stop) em toda operação (teste existente `test_custos_entram_em_toda_operacao`).
  - **Defeito corrigido (M5):** o walk-forward escolhia a combinação "menos ruim" mesmo quando TODAS perdiam no treino. Nos 5 meses a melhor média de treino foi de −0,324% a −0,414% por operação, e mesmo assim operava: 13 operações, −4,21%. Agora o mês só opera se a melhor combinação tiver média > `backtest.expectativa_min_treino_pct` (0). Resultado atual: 0 operações, com o motivo registrado em cada mês. Commit `54375c3`.
  - Intervalo de 95% da média do treino gravado em cada escolha do walk-forward.
  - Tempo do backtest completo: 67 s (era 250 s antes da Etapa 5).
- [x] **6.3** Métricas: retorno, win rate, drawdown máximo, Sharpe, nº de operações, comparação com buy-and-hold.
  - Já existiam: retorno, acerto, drawdown, Sharpe, operações (e por dia), fator de lucro, média por operação, tempo médio, pior sequência, taxas pagas, saídas por motivo, comprar e segurar.
  - **B4 corrigido:** a série diária do Sharpe usava dias UTC; uma operação na véspera UTC (dia local) era descartada. Teste novo: no código antigo o Sharpe dava NaN, no novo é calculado. Nos dados atuais o Sharpe não mudou (−2,33).
  - **B5 corrigido:** comprar e segurar usava 0,1% fixo; agora usa a taxa, o spread médio e o slippage do backtest, com a mesma conta de uma operação. ETHBRL: +24,07% → +24,02%.
  - Novo: `expectativa_ic95_pct` (intervalo de 95% da média por operação) em todas as métricas.
  - M7 documentado no código: dias sem operação contam como 0 no Sharpe; anualização com 365 dias.
  - Novo baseline do backtest rápido de referência (custos congelados): SHA-256 `ac4ae271f65f6e88374a85e6e8e64f3ed4107475dc937f7a87a934ec7186df65` (mudou de propósito por B5 e pela coluna nova; números das operações iguais). Commit `a93ebf9`.
- [x] **6.4** Regra de confiança: ordem firme só acima do limiar configurável; senão "AGUARDAR".
  - Camada 1 (já existia, agora testada): `estrategia.confianca_minima` por regime; teste prova que o limiar é configurável e que no limite exato compra.
  - Camada 2 (nova, `engine/reliability.py`): compra ao vivo só é firme se o último backtest do par, feito com os MESMOS parâmetros (hash da procedência) e com até 45 dias, tiver ≥ 30 operações fora da amostra, média > 0, fator de lucro ≥ 1 e o intervalo de 95% da média todo acima de 0 (seção `confiabilidade` do config). Senão a decisão vira ESPERAR com a trava "AGUARDAR (estratégia em teste): <motivos>". Vendas nunca são bloqueadas. Não se aplica ao backtest nem ao paper trading (onde a estratégia é testada).
  - Hoje, com o seu `data/reports/backtest.json`: "AGUARDAR (estratégia em teste): o último backtest não tem o registro de procedência (feito numa versão anterior; rode de novo)". Com um backtest novo, o ETHBRL reprovaria nos 4 critérios (13 operações, −0,280%, fator 0,36, intervalo começando em −0,626%).
  - 12 testes em `tests/test_reliability.py`. Commits `fbb8a73` e `774774c`.
- [x] **6.5** Reprodutibilidade (seed fixa, parâmetros versionados, modelo salvo).
  - `backtest/provenance.py`: `backtest.meta.json` e `modelo.meta.json` ao lado dos relatórios, com commit e alterações não commitadas, versões do Python e das bibliotecas, parâmetros (estratégia, indicadores, regime, backtest, modelo, fuso) e o hash deles, custos (e hash do arquivo congelado), linhas/período/SHA-256 de cada arquivo de histórico lido, semente e hash dos arquivos gerados.
  - Semente fixa `modelo.semente: 42`. O modelo é salvo em JSON legível (sem pickle). Três treinos seguidos geraram o mesmo arquivo de modelo, byte a byte (SHA-256 `d5b10af2…70ed89`).
  - **Modelo desafiador (D2):** pacote `model/` e comando `cryptoagent modelo treinar` (também no `--demo`). Conjunto de dados pelo mesmo simulador do backtest: 1.033 momentos candidatos no ETHBRL, 32,1% lucrativos depois dos custos. Treino mês a mês só com rótulos já conhecidos (teste automático). Fora da amostra: AUC 0,486 (sorteio = 0,5); Brier 0,256, pior que a referência 0,224; no simulador, 30 operações e −3,68% contra 13 e −3,67% das regras; comprar e segurar +24,02%. O modelo NÃO supera as regras; fica como desafiador para a Etapa 8. Commit `4ff6a88`.
  - Simulador ganhou ganchos opcionais (`observer`, `entry_rule`, `open_positions`); com os valores padrão o relatório de referência saiu com o mesmo hash.
- [ADIADO] **Quebrar `simulate` (220 linhas) e `decide` (122 linhas)** (pendência da Etapa 3, prevista para esta etapa). Não fiz: esta etapa já mexeu muito no simulador, e quebrar agora misturaria refatoração com mudança de comportamento no mesmo período. Proponho fazer na Etapa 11 (qualidade e lint), com o hash do backtest e a foto do teste ao vivo como prova de que nada mudou.
- **Decisão pendente (nome da ação):** o projeto usa a ação ESPERAR em todo lugar (banco, painel, terminal); o roteiro fala em "AGUARDAR". Por isso a trava nova aparece como "AGUARDAR (estratégia em teste)" dentro de uma decisão ESPERAR. Renomear a ação para AGUARDAR em todo o projeto é possível; aguardo sua decisão.

### Verificação final da Etapa 6
- `python -m pytest -q` → `298 passed, 1 warning in 21.93s` (eram 270; +28 testes).
- Backtest rápido de referência: SHA-256 `ac4ae271…7186df65` antes e depois das mudanças de modelo, procedência e confiabilidade (só as correções B4/B5/IC mudaram o arquivo).
- `--help`: diferenças em relação à Etapa 5 = comando novo `modelo` e a menção a `modelo treinar` na ajuda do `--demo`.

### Problemas durante a Etapa 6
- O primeiro `modelo treinar --demo` gravou o modelo em `data/models` (o config do demo não apontava a pasta do modelo). Apaguei o arquivo e a pasta, criados por essa execução, e o demo passou a usar `data/demo/models`.
- Um comando meu ficou esperando entrada do teclado (`cat > /dev/null` sem arquivo); parei a tarefa. Nada foi alterado.
- Os rótulos de mês do walk-forward apareciam no fuso de Brasília ("04/2026" para o mês de maio, que começa às 21h de 30/04 em Brasília); passaram a ser mostrados em UTC, o fuso das janelas.

## ETAPA 7 — SIMULAÇÃO COM DINHEIRO VIRTUAL

Backup antes da etapa: tag git `backup-antes-etapa-7` (aponta para `2088f2f`).

- [x] **7.1** Carteira virtual configurável (saldo inicial em R$ e perda máxima do dia definidos pelo usuário), separada da real.
  - **SIM1:** banco próprio da simulação, `data/simulacao.sqlite` (`paper.banco`), criado por `storage/sim_db.py`. Ele não tem nenhuma tabela da conta real; um teste confere isso. O paper e o replay nunca abrem o banco da conta: um teste aponta `conta.banco` para um arquivo que não pode ser criado, e ele não é. O painel lê a carteira virtual desse banco em modo somente leitura.
  - **SIM6:** `paper.capital` (R$) e `paper.perda_max_dia_brl` (R$ fixos, substitui o percentual), também por `paper rodar --capital 500 --perda-max 15`. O par precisa ser cotado em R$ (validação) e a perda precisa ser menor que o capital. O capital de uma carteira já existente não muda sozinho; o programa avisa e indica `paper resetar`.
  - O histórico antigo do paper (no banco da conta: 3 carteiras, 1 operação, 857 sinais) **não foi mexido nem copiado**. Contei só em modo leitura. Ver decisão pendente abaixo.
- [x] **7.2** Operar com dados de mercado reais (somente leitura) no perfil intraday/spot/reversão à média.
  - Execução real de `paper rodar --ciclos 6 --capital 500 --perda-max 15` (banco temporário fora de `data/`): leu as regras de ordem do ETHBRL na Binance ("mínimo R$ 10,00 por ordem, lote 0.0001"), analisou às 16:01 e 16:05 com dados ao vivo (ESPERAR: "preço não está esticado"), carteira R$ 500 / perda máxima R$ 15 gravadas. A taxa veio do config porque a chave da Binance está sendo recusada (−2015).
  - Mesmo motor do `analisar` e do backtest (`decide`); só candles fechados; spot, só compra, VENDER = sair da posição.
- [x] **7.3** Execução realista: taxas, slippage, spread, tamanho mínimo de ordem, atraso de execução.
  - Taxa, meio spread e slippage (mercado e stop) já eram os do backtest (`backtest/execution.py`); confirmados por teste.
  - **SIM5:** `paper/filters.py` aplica LOT_SIZE (mínimo e passo, arredonda para baixo), MARKET_LOT_SIZE (máximo a mercado) e NOTIONAL (valor mínimo). Valores reais do ETHBRL lidos de `/api/v3/exchangeInfo` em 11/11/2026: lote mínimo 0,0001, passo 0,0001, máximo a mercado 10,44144375 ETH, valor mínimo R$ 10. Cópia em `exemplos/filtros_binance.json` para uso sem internet. Ordem recusada não abre e fica em `paper_events`.
  - **Atraso de execução:** `paper.atraso_execucao_s` (1 s). Depois da decisão o laço espera e relê o melhor preço; a ordem virtual é executada nesse preço novo. Ficam gravados o preço da decisão e o executado.
  - **B1 corrigido:** a OCO virtual só confere candles de 1m que começaram depois da execução. O teste usa uma compra 5 s depois do início do minuto, com a mínima desse minuto abaixo do stop: o minuto não conta, o seguinte sim.
- [x] **7.4** Registrar TODA operação simulada em SQLite.
  - **SIM4:** `paper_trades` guarda:
    - horário e preço da decisão, horário e preço da execução;
    - quantidade já ajustada ao lote, valor em R$ e taxas;
    - stop e alvos, confiança e regime;
    - motivo da entrada (texto) e as 24 variáveis do momento (as mesmas do modelo desafiador);
    - cenário de mercado: regime, hora e dia da semana locais, spread, funding, percentil de volatilidade e parcelas da confiança;
    - decisão e execução da saída, motivo da saída e resultado em R$.

    Cada decisão (também as ESPERAR) continua em `signals`.
  - Exemplo real (replay da amostra, 08/09/2026): decisão às 10:15:05 a R$ 12.658,14; execução às 10:15:06 a R$ 12.661,94; 0,0789 ETH (R$ 999,03); stop às 10:23; resultado −R$ 4,26 com R$ 2,00 de taxas; variáveis e cenário gravados.
- [x] **7.5** Modo replay com dados históricos acelerados, reset da simulação e comparação com buy-and-hold.
  - **SIM3:** `paper replay --inicio AAAA-MM-DD --dias N`, também no `--demo`. `paper/replay.py` troca só o cliente da Binance por um que serve o histórico num relógio simulado; o laço do paper e o motor são os do modo ao vivo.
    - O transporte HTTP recusa qualquer requisição e endpoint de conta não é atendido (testes).
    - Só candles fechados; preço = abertura do minuto em curso ± meio spread da tabela de custos.
    - Derivativos indisponíveis e perfil por horário desligado (seria olhar o futuro).
    - Um banco por execução, recomeçado a cada vez.
  - Velocidade: 1 dia em 30 s na amostra (~2.900× o tempo real); 7 dias com a configuração normal em 299 s (~2.000×), com 2.016 decisões.
  - Reprodutível: duas execuções do mesmo período dão operações idênticas (teste).
  - **Replay x backtest** (mesmos custos, mesmo período):
    - Amostra, 08/09: replay 2 operações, +0,95%; backtest 3 operações, +0,47%.
    - Real, 01 a 07/09: replay 0 operações; backtest 1 (−0,71%).
    - A diferença vem dos avisos que só existem no replay (derivativos e perfil por horário), que reduzem a confiança em 5% cada.
    - **Prova:** com `penalidade_por_aviso_dados: 0` no mesmo replay, ele faz a mesma operação do backtest. Mesmo minuto (01/09 15:20), mesma saída (stop), −0,65% contra −0,71%.
  - **Reset:** `paper resetar` só mostra o que faria; com `--confirmar`, guarda uma cópia consistente do banco (`simulacao_antes_reset_<data>.sqlite`) e recomeça (teste).
  - **SIM7:** o relatório do paper e o do replay mostram comprar e segurar no mesmo período com os mesmos custos, e quantas ordens foram recusadas por tamanho.
- [ADIADO] **Painel no modo demo** (pendência da Etapa 4): o replay é o caminho para isso, mas mostrar o replay no painel é trabalho da tela (Etapa 10).
- **Decisão pendente:** copiar o histórico antigo do paper (3 carteiras, 1 operação, 857 sinais, hoje dentro de `data/agent.sqlite`) para o banco novo da simulação? O formato antigo não tem preço de decisão, variáveis nem cenário; esses campos ficariam vazios. Não fiz sem a sua autorização.

### Verificação final da Etapa 7
- `python -m pytest -q` → `310 passed, 1 warning in 31.43s` (eram 298; +12 testes).
- Backtest rápido de referência: SHA-256 `ac4ae271…7186df65`, igual ao da Etapa 6 (o backtest não mudou).
- `ruff check --select F,E9` em `src` e `tests`: sem erros. Achou um erro real que os testes não pegavam: `dashboard/service.py` usava `sqlite3` sem importar, e o painel quebraria assim que existisse banco da simulação. Corrigido e coberto por teste novo. Corrigiu também 2 avisos deixados na Etapa 6 (f-string sem variável, import sem uso).

### Problemas durante a Etapa 7
- Na primeira versão, as variantes do paper eram montadas a partir do `config.yaml` puro, perdendo o `--sobrepor` e o `--demo`. No demo, a variante "padrão" analisaria o ETHUSDT de `data/history`. Corrigido com `apply_overlay`: a sobreposição de cada variante é aplicada sobre o config em uso.
- O replay com a configuração normal ficou mais conservador que o backtest (avisos de dados que só existem nele); medido e documentado acima, não "corrigido" no código.

## ETAPA 8 — APRENDIZADO CONTÍNUO E PANORAMAS CONFIÁVEIS

Backup antes da etapa: tag git `backup-antes-etapa-8` (aponta para `a088e4e`). Pacote novo: `src/cryptoagent/learning/`.

- [x] **8.1** Ciclo de reavaliação periódica (fim do dia ou a cada N operações) que ajusta/retreina com os dados da simulação.
  - `learning/cycle.py` (`cryptoagent aprendizado reavaliar [--ate AAAA-MM-DD]`):
    - monta os momentos candidatos pelo mesmo simulador do backtest;
    - acrescenta as operações da simulação posteriores ao fim do histórico;
    - retreina o modelo mês a mês e avalia todos os candidatos no mesmo período fora da amostra;
    - mede mudança de regime e grava registro, panorama e relatório.
  - **Ponto no tempo:** com `--ate`, só entram dados anteriores à data: janelas, rótulos e operações que já tinham terminado. Testado.
  - **Automático:** `paper rodar` dispara o ciclo em segundo plano (outra thread, sem travar o laço) na virada do dia local e a cada `aprendizado.reavaliar_a_cada_operacoes` (20) operações virtuais fechadas. Os marcadores ficam no banco da simulação. Teste com os dois gatilhos.
  - **Uso dos dados da simulação:** (a) operações virtuais com variáveis registradas e decididas depois do fim do histórico entram no treino; (b) o resultado da carteira virtual é comparado com o intervalo do campeão no backtest (alerta "simulação x backtest", a partir de 10 operações). Hoje há 0 operações nessas condições, então ainda não houve efeito real.
- [x] **8.2** Esquema campeão/desafiador.
  - **Desafiantes de cada ciclo:**
    - as hipóteses declaradas em `aprendizado.hipoteses` (hoje `config/hipotese_setup_1h.yaml`);
    - o modelo de regressão logística, retreinado a cada ciclo;
    - os ex-campeões.
  - **Comparação:** todos contra o campeão, no MESMO período fora da amostra, com custos.
  - **Ao vivo:** o campeão é usado no `analisar`, no painel e na variante "padrao" do paper. Uma hipótese promovida troca a estratégia; o modelo promovido decide a compra nos momentos candidatos, sem passar por cima das travas de segurança, de custo e da regra de confiabilidade.
  - **Demonstração ponto no tempo no histórico real** (6 ciclos: até 01/06, 01/07, 01/08, 01/09, 01/10 e o fim do histórico, 22/10/2026; ~28 s cada):
    - **nenhuma promoção**: os desafiantes foram rejeitados nos 6 ciclos, cada vez com os motivos registrados;
    - **hipótese do setup 1h:** só 2 operações fora da amostra (+1,215% por operação; intervalo de −3,691% a +6,120%). Amostra insuficiente.
    - **modelo:** 30 operações e −0,129% por operação no último ciclo. Rejeitado por média negativa e retorno pior que o campeão (−3,68% x −3,67%).
  - **Promoção:** como os dados reais não promoveram ninguém, a mecânica foi provada num teste que força o veredito. O campeão é trocado, o anterior vira "aposentado" e a análise ao vivo passa a usar o novo. **Não houve promoção real.**
- [x] **8.3** Histórico de versões do modelo com métricas.
  - `learning/registry.py`: `registro.json` legível, com cada versão (tipo, origem, estado) e cada avaliação. A avaliação guarda ciclo, dados até, período fora da amostra, métricas, hash dos parâmetros, hash dos dados, decisão e motivos. Os modelos de cada ciclo ficam em `modelos/modelo-cicloNNN.json`.
  - `cryptoagent aprendizado versoes` mostra a tabela. Dois ciclos com os mesmos dados dão métricas idênticas (teste).
- [x] **8.4** Proteções.
  - **Mínimos antes de confiar:** critérios de promoção (30 operações e 60 dias fora da amostra) e a regra de confiabilidade da Etapa 6, que agora usa as métricas do campeão quando ele não é o padrão.
  - **Mudança de regime (`learning/drift.py`):** PSI de regime, ATR% e tamanho dos movimentos do 15m, e das variáveis dos momentos candidatos.
    - Só alerta se o PSI passar de 0,25 **e** o último mês for incomum frente às outras janelas de 30 dias do período (p < 0,05).
    - Com alerta, nada é promovido e a análise ao vivo ganha um aviso de dados (−5% na confiança).
  - **Calibração do alerta, com números:**
    - **1ª versão:** alertou nos 6 ciclos. Um PSI de amostra pequena (51 casos) dá ~0,20 só por acaso, e olhar o maior de 17 variáveis aumenta isso.
    - **2ª versão** (desconto do acaso + teste de permutação): ainda 6 de 6. Casos vizinhos no tempo se parecem, e a permutação ignora isso.
    - **Versão final** (comparação com as janelas do próprio período): 1 de 6 ciclos, em 01/06 (ATR% com PSI 2,20, p = 0,04).
    - Testes provam que, com dados sem mudança, há no máximo 1 alarme falso em 10, e que uma mudança real é detectada.
  - **Cenário inédito:** cenário = regime | período do dia | faixa de volatilidade.
    - Com menos de 30 casos no histórico, a confiança ao vivo é multiplicada por um fator que vai de 0,7 (nenhum caso) até 1, aparecendo como parcela "cenário".
    - O backtest não aplica esse fator, porque precisaria das contagens ponto no tempo. É uma diferença que só deixa o ao vivo mais conservador.
- [x] **8.5** Panoramas e calibração com gráfico.
  - `learning/panorama.py`, sobre os 1.033 momentos candidatos do histórico real, em 42 cenários. Para cada cenário: casos, acerto com intervalo de 95% (Wilson), ganho médio, perda média e média por operação com intervalo de 95%.
  - **Ao vivo:** o `analisar` mostra o panorama do cenário do momento.
  - **Saídas:** `aprendizado panorama` e `aprendizado.html` (tabela por cenário e dois diagramas de calibração em SVG, regras e modelo, com barras de intervalo de 95%).
  - **Resultado real da calibração:** a confiança das regras NÃO acompanha o acerto. Faixas 0–40%: 32%; 40–55%: 33%; 55–70%: 34%; 70–85%: 20% (35 casos); 85–100%: 30% (23 casos). Isso confirma M4 do diagnóstico: a confiança é uma pontuação, não uma probabilidade.
  - **Cuidado registrado:** o cenário "alta_volatilidade | noite | volatilidade alta" mostra +0,450% por operação (IC +0,063% a +0,837%, 110 casos). São 42 cenários olhados de uma vez, e casos vizinhos se parecem, então o intervalo real é mais largo. Isso é descrição do passado, não vantagem validada. Para virar estratégia, teria de ser uma hipótese declarada e testada fora da amostra.
- [x] **8.6** Critérios de promoção configuráveis; enquanto não atingidos, mostrar "AGUARDAR / EM TESTE".
  - Seção `aprendizado` do config. Todos os critérios precisam passar: operações e dias mínimos, média ≥ campeão + 0,05 ponto, média > 0, retorno > campeão, drawdown não pior, sem mudança de regime. Cada um tem teste próprio, e o caso "menos ruim, mas ainda no prejuízo" não é promovido.
  - "AGUARDAR / EM TESTE": a regra de confiabilidade da Etapa 6 continua mostrando "AGUARDAR (estratégia em teste)" com os motivos, agora para o campeão vigente.
- **Decisão minha que precisa do seu aval:** a calibração do alerta de mudança de regime foi feita depois de ver os resultados no histórico real (3 versões, todas descritas acima). Cada ajuste tem justificativa estatística e teste, mas foi motivado pelos dados. Se preferir um alerta mais sensível, `aprendizado.psi_alerta` e `bloquear_promocao_com_drift` ficam no config.

### Verificação final da Etapa 8
- `python -m pytest -q` → `333 passed, 1 warning in 51.56s` (eram 310; +23 testes: 22 em `tests/test_learning.py` e 1 no demo).
- `ruff check --select F,E9`: sem erros.
- Backtest rápido de referência: SHA-256 `ac4ae271…7186df65`, igual ao da Etapa 7 (o backtest não mudou).
- Modo demo: `aprendizado reavaliar`, `versoes` e `panorama` rodam sem rede e sem chave (teste).

### Problemas durante a Etapa 8
- O alerta de mudança de regime precisou de 3 versões (ver 8.4).
- Os testes da análise ao vivo e do replay passaram a apontar a pasta do aprendizado para um lugar vazio. Antes, um ciclo rodado no demo mudaria a "foto" do teste ao vivo.
- `extract_features` passou a tolerar coluna ausente (vira NaN, e o modelo usa a média do treino). Apareceu ao aplicar o modelo nos cenários de teste do motor, que não têm todas as colunas.
- Não consegui tirar print do relatório HTML local no navegador embutido (ele não opera arquivos locais). O gráfico é verificado por teste (SVG com um ponto por faixa) e pelo arquivo gerado.

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

### Etapa 4
- Criados: `src/cryptoagent/data/retry.py`, `src/cryptoagent/data/validation.py`, `config/demo.yaml`, `scripts/gerar_amostra_demo.py`, `exemplos/LEIAME.md`, `exemplos/custos_demo.json`, `exemplos/historico/ETHBRL/1m.parquet`, `5m.parquet`, `15m.parquet`, `1h.parquet`, `4h.parquet`, `tests/test_validation.py`, `tests/test_demo.py`
- Alterados: `.gitignore`, `config/config.yaml`, `src/cryptoagent/config.py`, `src/cryptoagent/data/rest.py`, `src/cryptoagent/data/history.py`, `src/cryptoagent/data/health.py`, `src/cryptoagent/engine/live.py`, `src/cryptoagent/backtest/data.py`, `src/cryptoagent/cli/app.py`, `src/cryptoagent/cli/historico.py`, `src/cryptoagent/cli/indicadores.py`, `tests/test_history.py`, `tests/test_timeutils.py`, `PROGRESSO.md`
- Removidos: nenhum
- Não tocados: `.env`, `data/history`, `data/*.sqlite`, `logs/`, `README.md`. Criada pelo programa (ignorada pelo git): `data/demo/` (relatórios e log do modo demo)
- Fora do repositório (temporários, no scratchpad da sessão): pasta `hist_incremental` da demonstração do 4.3, scripts de medição

### Etapa 5
- Criados: `scripts/medir_desempenho.py`, `src/cryptoagent/intervals.py`, `src/cryptoagent/cli/_console.py`, `src/cryptoagent/indicators/cache.py`, `tests/fake_binance.py`, `tests/test_live.py`
- Alterados: `src/cryptoagent/timeutils.py`, `src/cryptoagent/cli/__init__.py`, `src/cryptoagent/cli/_shared.py`, `src/cryptoagent/cli/app.py`, `src/cryptoagent/engine/context.py`, `src/cryptoagent/engine/decision.py`, `src/cryptoagent/engine/rules.py`, `src/cryptoagent/engine/signals.py`, `src/cryptoagent/engine/live.py`, `src/cryptoagent/backtest/data.py`, `src/cryptoagent/backtest/simulator.py`, `tests/test_demo.py`, `PROGRESSO.md`
- Removidos: nenhum
- Não tocados: `.env`, `data/`, `logs/`, `config/`, `README.md`, `exemplos/`
- Fora do repositório (temporários, no scratchpad da sessão): `medicao_antes.json`, `medicao_depois.json`, `ao_vivo_antes.json`, scripts de profiling

### Etapa 6
- Criados: `docs/AUDITORIA_BACKTEST.md`, `src/cryptoagent/backtest/provenance.py`, `src/cryptoagent/cli/modelo.py`, `src/cryptoagent/engine/reliability.py`, `src/cryptoagent/model/__init__.py`, `dataset.py`, `features.py`, `logistic.py`, `walkforward.py`, `tests/test_model.py`, `tests/test_reliability.py`, `tests/test_sem_futuro.py`
- Alterados: `config/config.yaml`, `config/demo.yaml`, `exemplos/LEIAME.md`, `pyproject.toml`, `src/cryptoagent/config.py`, `src/cryptoagent/backtest/metrics.py`, `report.py`, `runner.py`, `simulator.py`, `src/cryptoagent/cli/app.py`, `cli/backtest.py`, `src/cryptoagent/engine/context.py`, `decision.py`, `live.py`, `tests/test_backtest.py`, `tests/test_demo.py`, `tests/test_engine.py`, `PROGRESSO.md`
- Removidos: nenhum do repositório. Fora dele: `data/models/ETHBRL/modelo-20261111-183246.json` e as pastas vazias `data/models/ETHBRL` e `data/models`, criados por engano pela minha execução do demo.
- Não tocados: `.env`, `data/history`, `data/*.sqlite`, `data/reports` (o seu relatório antigo continua lá), `logs/`, `README.md`
- Instalado no `.venv`: `scikit-learn` 1.9.1 (com `scipy` 1.18.1 e dependências)
- Fora do repositório (temporários): cópia do código antigo via `git worktree` (removida), scripts de auditoria e relatórios no scratchpad

### Etapa 7
- Criados: `src/cryptoagent/storage/sim_db.py`, `src/cryptoagent/paper/filters.py`, `src/cryptoagent/paper/replay.py`, `exemplos/filtros_binance.json`, `tests/test_replay.py`
- Alterados: `config/config.yaml`, `config/demo.yaml`, `exemplos/LEIAME.md`, `src/cryptoagent/config.py`, `src/cryptoagent/storage/agent_db.py`, `src/cryptoagent/paper/book.py`, `paper/runner.py`, `paper/report.py`, `src/cryptoagent/cli/paper.py`, `cli/app.py`, `cli/modelo.py`, `src/cryptoagent/data/rest.py`, `src/cryptoagent/engine/live.py`, `src/cryptoagent/dashboard/service.py`, `scripts/medir_desempenho.py`, `tests/test_paper.py`, `tests/test_dashboard.py`, `tests/test_live.py`, `tests/test_model.py`, `docs/AUDITORIA_BACKTEST.md`, `PROGRESSO.md`
- Removidos: nenhum
- Não tocados: `.env`, `data/agent.sqlite` (só lido, para contar o histórico antigo do paper), `data/history`, `data/costs.sqlite`, `logs/`, `README.md`
- Criados pelo programa fora do repositório: `data/demo/replay/*.sqlite` e `data/demo/reports` (modo demo, ignorados pelo git); bancos de teste do paper ao vivo e do replay real ficaram no scratchpad

### Etapa 8
- Criados: `src/cryptoagent/learning/__init__.py`, `registry.py`, `criteria.py`, `drift.py`, `panorama.py`, `cycle.py`, `state.py`, `src/cryptoagent/cli/aprendizado.py`, `tests/test_learning.py`
- Alterados: `config/config.yaml`, `config/demo.yaml`, `src/cryptoagent/config.py`, `src/cryptoagent/model/walkforward.py`, `model/dataset.py`, `model/features.py`, `src/cryptoagent/engine/context.py`, `engine/decision.py`, `engine/live.py`, `engine/reliability.py`, `src/cryptoagent/paper/runner.py`, `src/cryptoagent/cli/app.py`, `cli/decisao.py`, `cli/paper.py`, `tests/test_live.py`, `tests/test_replay.py`, `tests/test_demo.py`, `docs/AUDITORIA_BACKTEST.md`, `exemplos/LEIAME.md`, `PROGRESSO.md`
- Removidos: nenhum
- Não tocados: `.env`, `data/` real (os ciclos da demonstração gravaram no scratchpad; `data/learning` não foi criado), `logs/`, `README.md`
- Criados pelo programa no modo demo: `data/demo/learning/` (ignorado pelo git)
