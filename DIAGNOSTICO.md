# DIAGNÓSTICO — IA_para_Criptos_2.0

Data: 2026-11-10 · Branch: `melhorias` · Commit analisado: `fb91f3b`
Etapa 1 do roteiro (somente leitura: nenhum arquivo de código foi alterado).

Tudo aqui vem de leitura do código real ou de execução real. Onde eu **não** verifiquei algo, está escrito "não verificado".

---

## 0. Resumo em uma página

**O projeto já está bem acima do que o roteiro presumia.** Não é um script solto: é um pacote Python (`src/cryptoagent`, 74 arquivos, 9.007 linhas) com 213 testes passando, cliente Binance somente leitura com lista fixa de endpoints, configuração validada, filtro de segredos nos logs, backtest walk-forward com custos e paper trading.

**Os três achados que mais importam:**

1. **A estratégia não tem vantagem comprovada, e o projeto diz isso com honestidade.** Backtest fora da amostra (30/04 a 30/09/2026), já com custos: ETHBRL −3,67% em 13 operações (acerto 23,1%, Sharpe −2,33) contra +24,07% de comprar e segurar. Para o portfólio isso não é defeito, desde que continue sendo apresentado sem maquiagem.
2. **Não existe "modelo adaptativo".** A decisão é um conjunto de regras fixas com uma pontuação (`engine/decision.py`). Não há modelo treinado, versão de modelo, esquema campeão/desafiador nem retreino. A Etapa 8 inteira é construção nova, não revisão.
3. **A chave da Binance está sendo recusada agora** (erro −2015 em `/sapi/v1/account/apiRestrictions`, visto na execução do backtest). Causa provável segundo o próprio README: IP público mudou. Isso é ação sua, não defeito do código.

**O que falta para publicar:** modo demo sem chave, licença, CI, lint, travar dependências, aba de simulação no painel, tema claro/escuro com botão, capturas de tela, e limpar do README o caminho pessoal `C:\Users\<usuário>\...`.

**Contagem de problemas:** 9 de prioridade alta, 31 média, 22 baixa.

---

## 1.1 Mapa do projeto

### Linguagem e dependências
- Python (`requires-python >=3.11`); ambiente com Python 3.14.7 e pip 26.2.2.
- Dependências diretas (`pyproject.toml:11-24`): httpx, websockets, pandas, numpy, pyarrow, pydantic, pyyaml, python-dotenv, rich, tzdata, fastapi, uvicorn. Dev: pytest.
- Frontend sem build: HTML/CSS/JS puros em `dashboard/static/`, gráfico pela biblioteca lightweight-charts carregada de CDN (`index.html:8`).

### Pontos de entrada
- `cryptoagent = "cryptoagent.cli:main"` (`pyproject.toml:30`) e `python -m cryptoagent` (`__main__.py`).
- 14 grupos de comandos em `cli.py:1179-1291`: `dados`, `historico`, `custos`, `indicadores`, `analisar`, `painel`, `alertas`, `paper`, `backtest`, `sinais`, `sazonalidade`, `sessao`, `conta`, `diario`.
- Atalhos Windows em `atalhos/*.ps1` (abrem coletor, paper e painel).

### Fluxo de dados (arquivos reais)

| Fase | O que acontece | Arquivos |
|---|---|---|
| Coleta | REST somente leitura (GET, lista fixa `ALLOWED_PATHS`), WebSocket, histórico mensal do data.binance.vision com SHA-256, Parquet local | `data/rest.py`, `data/websocket.py`, `data/history.py`, `data/market_store.py`, `data/models.py` |
| Saúde | Idade de preço/candle/derivativos, desvio de relógio; dado ruim vira ESPERAR | `data/health.py` |
| Processamento | EMA, RSI, MACD, Stoch RSI, ATR, Bollinger, ADX, VWAP do dia, pivôs, suporte/resistência, regime | `indicators/*.py` |
| Contexto de horário | Perfil por hora validado em walk-forward; só reduz confiança | `seasonality/*.py`, `backtest/season_pit.py` |
| Custos | Coletor de spread/profundidade ao vivo em SQLite; custo de ida e volta | `costs/*.py`, `seasonality/cost_model.py` |
| Conta (real, leitura) | Permissões, saldo, operações, resultado do dia, limites, sessão do dia | `account/*.py`, `session/daily.py` |
| "Modelo" (regras) | Travas → sinais (VWAP, %B, RSI) → gatilho anti-faca → níveis → confiança → COMPRAR/VENDER/ESPERAR | `engine/decision.py`, `engine/rules.py`, `engine/signals.py`, `engine/context.py`, `engine/live.py` |
| Registro de sinais | Tabela `signals` em SQLite | `engine/journal.py`, `storage/agent_db.py` |
| Backtest | Mesmo `decide()` do ao vivo, execução no 1m, walk-forward em grade, ablação, comparações | `backtest/*.py` |
| Simulação | Carteira virtual por variante, OCO virtual, relatório | `paper/book.py`, `paper/runner.py`, `paper/report.py` |
| Dashboard | Serviço ao vivo + FastAPI em 127.0.0.1 + página estática | `dashboard/service.py`, `dashboard/server.py`, `dashboard/static/*` |
| Alertas | Terminal, bipe, notificação do Windows | `alerts/notify.py` |

Os sinais são calculados no ETHUSDT e os preços traduzidos para o par de operação pela razão entre os dois (`engine/live.py:133-134`).

### Bancos locais (esquema lido em modo somente leitura; conteúdo pessoal não foi lido)
- `data/agent.sqlite`: `account_trades` (0 linhas), `manual_trades` (0), `daily_sessions` (2), `signals` (3.539), `paper_state` (3), `paper_trades` (1), `meta`.
- `data/costs.sqlite`: `book_samples` (98.683), `book_snapshots` (1.677), `collector_events` (319).

---

## 1.2 Execução real: projeto e testes

Máquina: Windows 11, Python 3.14.7. Todos os tempos são de relógio, medidos nesta sessão.

| Comando | Resultado | Tempo |
|---|---|---|
| `python -m pytest -q --durations=10` | **213 passed, 1 warning** | 23,07 s (pytest) / 25,79 s (total) |
| `cryptoagent --help` | exit 0 | 1,58 s |
| `import cryptoagent.cli` (`-X importtime`) | 0,99 s acumulado | 1,18 s |
| `cryptoagent historico verificar` | exit 0, 0 buracos em 14 séries | 1,39 s |
| `cryptoagent custos resumo` | exit 0, "Só 6 dia(s) de coleta (mínimo 7)" | 1,35 s |
| `cryptoagent dados status` | exit 0, "Dados em ordem" | 9,80 s |
| `cryptoagent backtest --rapido --pares ETHBRL` (relatório gravado fora do projeto) | exit 0 | 32 s de simulação |
| `cryptoagent paper relatorio` | exit 0 | 12 s |

**Aviso único dos testes:** `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead` (`fastapi/testclient.py:1`).

**Teste mais lento:** `test_atalhos_dao_exatamente_o_mesmo_resultado_que_chamar_o_motor_em_tudo`, 7,80 s (34% da suíte).

**Saída real do backtest rápido (ETHBRL, fora da amostra 30/04/2026 a 30/09/2026):**

| Variante | Operações | Retorno | Drawdown máx. | Acerto | Fator de lucro | Sharpe |
|---|---|---|---|---|---|---|
| Parâmetros padrão | 13 | −3,67% | −4,45% | 23,1% | 0,36 | −2,33 |
| Versão ingênua (sem filtros) | 598 | −76,40% | −76,85% | 34,4% | 0,39 | −12,16 |
| Trava de distância 1,5× | 13 | −3,67% | −4,45% | 23,1% | 0,36 | −2,33 |
| Trava de distância 1× | 13 | −3,67% | −4,45% | 23,1% | 0,36 | −2,33 |
| Comprar e segurar | — | +24,07% | −33,80% | — | — | — |

**Relatório completo já salvo** (`data/reports/backtest.json`, gerado antes desta sessão): ETHUSDT padrão −6,615% em 18 operações; walk-forward −6,478% em 17. ETHBRL padrão −3,652% em 13; walk-forward −4,195% em 13. Hipótese 1h: 2 operações por par, walk-forward com 0 operações.

**Saída real do paper trading:** 3 variantes, **0 operações virtuais fechadas**, 1 posição virtual aberta na variante de estudo. Sinais registrados na variante padrão: 472, todos ESPERAR.

**Erros observados durante a execução:**
- `Não foi possível ler as taxas da conta (... endpoint /sapi/v1/account/apiRestrictions, status 400, código -2015)`. O programa tratou e seguiu com as taxas do config.

**O que eu NÃO executei, e por quê:**
- `analisar`, `conta status`, `conta permissoes`, `painel`, `sessao`, `sinais`: leem ou exibem saldo e operações reais. Não quis trazer dados pessoais para a conversa.
- `paper rodar`, `custos coletar`: processos contínuos que gravam nos seus bancos.
- `historico baixar`, `sazonalidade analisar`: sobrescrevem arquivos em `data/`.
- Consequência: **o painel não foi aberto nesta etapa.** A análise de UI abaixo vem da leitura de `index.html`, `style.css`, `app.js` e dos 8 testes de `tests/test_dashboard.py`.

---

## 1.3 Segredos no código e no histórico git

Varredura nos 2 commits existentes, sem imprimir valores.

| Verificação | Resultado |
|---|---|
| Sequência de 64 caracteres alfanuméricos (formato de chave Binance) | nenhuma |
| `sk-ant-...` | só `tests/test_redact.py` (valor fictício do teste do filtro) |
| `AKIA...`, chave privada PEM, token do GitHub, CPF | nenhum |
| Atribuição `API_KEY/SECRET/TOKEN/PASSWORD=valor` | `tests/test_dashboard.py:15` (token fictício) e `index.html:137` (marcador `__TOKEN__`, substituído em tempo de execução) |
| `.env`, `data/` ou `logs/` alguma vez versionados | 0 commits |
| `.env.example` com valores preenchidos | 0 linhas |
| E-mail de autor no histórico | só o noreply do GitHub |
| Caminho pessoal em arquivos rastreados | `README.md:22`, `:29`, `:145` (`C:\Users\<usuário>\Documents\...`) |

**Conclusão: nenhum segredo no código nem no histórico.** Não há chave para revogar por vazamento no repositório.

O `.env` real define três variáveis (li só os nomes): `BINANCE_API_KEY`, `BINANCE_API_SECRET`, `ANTHROPIC_API_KEY`. A terceira é lida em `config.py:476` e não é usada por nenhum outro módulo.

---

## 1.4 Problemas por categoria

Prioridade: **A** alta · **M** média · **B** baixa.

### Bugs

| # | Pri | Onde | Problema | Como corrigir |
|---|---|---|---|---|
| B1 | M | `paper/book.py:153-154` | A OCO virtual inclui o candle de 1m em que a compra aconteceu. A mínima/máxima desse candle anteriores à entrada (os primeiros segundos, dado `atraso_s: 5`) podem disparar stop ou alvo que não teriam ocorrido. | Conferir só candles com `open_time >= entry_ms`, ou tratar o primeiro candle à parte. Cobrir com teste. |
| B2 | M | `backtest/simulator.py:248` vs `:282` | Com posição aberta, o backtest passa lista vazia de suportes/resistências; sem posição, passa `levels_at(t)`. O ao vivo passa sempre (`engine/live.py:202`). Não verifiquei se alguma regra de saída usa esses níveis; se usar, backtest e ao vivo divergem. | Auditar na Etapa 6. Se não houver uso, documentar; se houver, igualar. |
| B3 | M | Saída do backtest (seção 1.2) | As três travas de distância (2,5×, 1,5×, 1×) deram resultados **idênticos**. Ou a trava nunca é a restrição ativa nesse período, ou a variação não está chegando ao motor. Não verifiquei qual. | Investigar na Etapa 6 com contagem de bloqueios por categoria em cada variante. |
| B4 | B | `backtest/metrics.py:67` | O Sharpe reindexa por datas em UTC, mas os dias das operações são locais (Brasília). Uma operação no dia local anterior à primeira data UTC seria descartada. Não verifiquei se ocorre nos dados atuais. | Gerar o intervalo de dias no mesmo fuso das operações. |
| B5 | B | `backtest/metrics.py:102` | Comprar e segurar usa taxa fixa de 0,1% por lado (`0.999 * 0.999`), ignorando a taxa configurada e o spread. | Receber a taxa e o spread por parâmetro. |
| B6 | B | `engine/decision.py:106`, `rules.py:155` | Funções lambda atribuídas a nome (`tp = lambda ...`). | Trocar por `def`. |

### Segurança

| # | Pri | Onde | Problema | Como corrigir |
|---|---|---|---|---|
| S1 | A | `README.md:22,29,145` | Caminho pessoal com nome de usuário do Windows em arquivo que vai a público. | Trocar por caminho genérico (Etapa 12). |
| S2 | M | `dashboard/server.py:50-60` | As rotas GET (`/api/estado`, `/api/candles`) não exigem token e o servidor não valida o cabeçalho `Host`. Só escuta em 127.0.0.1, mas `/api/estado` devolve saldo, posição e operações reais; um ataque de DNS rebinding conseguiria ler. | Validar `Host` (127.0.0.1/localhost) em todas as rotas. |
| S3 | M | `index.html:8` | Script de terceiros carregado de CDN sem `integrity` (SRI), numa página que mostra dados da conta. | Adicionar SRI ou servir a biblioteca localmente (também resolve o painel sem internet). |
| S4 | M | `config.py:421,429,476`, `.env.example:10-12` | `ANTHROPIC_API_KEY` é pedida e carregada, mas nenhum código a usa. Pedir uma chave desnecessária é superfície de risco e confunde quem instala. | Remover do código e do `.env.example`. Você pode apagar essa linha do seu `.env`. |
| S5 | B | Todo o projeto | Não existe código de envio de ordens, então a regra "execução real atrás de flag" está atendida por ausência. Falta só deixar isso explícito na documentação. | Documentar na Etapa 2.2; manter os testes de `test_readonly_guard.py`. |
| S6 | B | `data/agent.sqlite` | Sinais, sessões, operações reais e carteira virtual ficam no mesmo arquivo. Está fora do git, mas dificulta montar dados de demonstração sem risco. | Ver SIM1. |

Pontos fortes confirmados na leitura: só GET e lista fixa de caminhos (`data/rest.py:65,161-163`); recusa de chave com permissão além de leitura, inclusive permissões futuras (`account/permissions.py:16,65`); segredos em `SecretStr` (`config.py:416-429`); filtro de segredos em log e na página (`logging_setup.py:14`, `dashboard/service.py:39-51`); token com comparação em tempo constante nas rotas que alteram algo (`dashboard/server.py:39-41`).

### Performance

| # | Pri | Onde | Problema | Como corrigir |
|---|---|---|---|---|
| P1 | M | `cli.py:94-131` | `dados status` levou 9,80 s. São cerca de 12 requisições feitas uma após a outra. | `asyncio.gather` nas consultas independentes. |
| P2 | M | `engine/live.py:122-146` | Na análise ao vivo, bookTicker (×2), profundidade e derivativos são sequenciais; só os candles são paralelos (`:109`). | Paralelizar. Medir antes e depois na Etapa 5. |
| P3 | M | `engine/live.py:115` | Os indicadores de todos os timeframes são recalculados do zero (até 1.000 candles cada) a cada análise. | Cache por (timeframe, último `close_time`). |
| P4 | M | `engine/live.py:173-174` | A cada análise com perfil de horário, o histórico de 15m é atualizado, o Parquet inteiro é regravado e relido. | Atualizar no máximo uma vez por candle de 15m e manter em memória. |
| P5 | B | `dashboard/service.py:281-286` | `candles_payload` usa `iterrows()` e calcula o fuso candle a candle. | Vetorizar. |
| P6 | B | `tests/test_backtest.py` | Um teste sozinho custa 7,80 s. | Reduzir os dados sintéticos ou marcar como lento. |
| P7 | B | Backtest | 32 s para um par no modo rápido; o modo completo roda mais 8 combinações de grade e 7 ablações em sequência. Tempo do modo completo: não medido. | Medir na Etapa 5; avaliar paralelismo por processo. |

### Qualidade do código

Medido por script AST sobre `src/cryptoagent`: 440 funções; 13 sem anotação de retorno; 11 com algum parâmetro sem anotação; **325 sem docstring (74%)**; 8 com mais de 80 linhas; 140 linhas com mais de 140 colunas; 22 `# type: ignore`.

| # | Pri | Onde | Problema | Como corrigir |
|---|---|---|---|---|
| Q1 | A | Raiz | Não há lint, formatador, pre-commit nem CI. Nenhuma dessas ferramentas está instalada no ambiente. | Etapa 11. |
| Q2 | M | `cli.py` (1.328 linhas) | Arquivo único com 25 comandos, tabelas e 32 imports dentro de função. | Dividir em `cli/` por grupo de comando. |
| Q3 | M | `simulator.py:115` (198 linhas), `live.py:74` (166), `status.py:86` (147), `decision.py:86` (118) | Funções longas com várias responsabilidades. | Extrair etapas, com teste de equivalência antes e depois. |
| Q4 | M | 325 funções | Sem docstring. Os módulos têm boa docstring de topo; as funções não. | Priorizar funções públicas (Etapa 3.2). |
| Q5 | M | `pyproject.toml:11-24` | Dependências só com versão mínima, sem arquivo de trava. Instalações futuras podem pegar versões incompatíveis. | Gerar trava (Etapa 12.2). |
| Q6 | B | `engine/rules.py:179`, `live.py:33`, `service.py:59,108`, `logging_setup.py:22`, `cli.py:269`, `history.py:141,156,223`, `metrics.py:102` | Números mágicos fora do config (stop de 2 ATR, limites de candles, 1.100, 300, 5 MB, timeout de 60 s, 3 tentativas). | Mover para o config (Etapa 2.3). |
| Q7 | B | Estrutura | Os nomes das pastas diferem dos pedidos no roteiro (`engine`, `paper`, `indicators` em vez de `models`, `simulation`, `features`). | Ver decisão D1 no plano. |
| Q8 | B | Raiz | Sem `.gitattributes`; o git avisa de conversão LF/CRLF em todos os arquivos. | Adicionar `.gitattributes`. |
| Q9 | B | `logging_setup.py:19` | O log é texto simples, não estruturado. Já é rotativo, em UTC e filtrado. | Opção de saída JSON (Etapa 2.4). |

Sobre `print`: não há `print()` solto usado como log. As 123 ocorrências do padrão são `console.print` da biblioteca rich, que é a interface do terminal. O item 2.4 do roteiro se reduz a estruturar o log que já existe.

### Coleta de dados

| # | Pri | Onde | Problema | Como corrigir |
|---|---|---|---|---|
| C1 | A | Projeto todo | **Não há modo demo/offline.** Sem internet e sem histórico baixado (cerca de 130 MB em `data/history`), nada roda. Quem clonar o repositório não consegue ver o projeto funcionando. | Etapa 4.5: amostra pequena de candles públicos versionada + flag de demonstração. |
| C2 | M | `data/models.py:50-60` | A normalização remove duplicados e ordena, e `find_gaps` acha buracos. **Não há** checagem de nulos, de coerência do candle (máxima ≥ mínima, preço > 0) nem de valores absurdos. | Função de validação com relatório (Etapa 4.2). |
| C3 | M | Saída de `historico verificar` | As séries terminam em datas diferentes: 15m de ETHUSDT/ETHBRL vai até novembro, as demais param em outubro; e os 1m de cada par terminam em horas diferentes. O backtest usa o que houver, sem avisar do desalinhamento. | Checar alinhamento ao carregar e avisar; comando único de atualização. |
| C4 | M | `data/history.py:141-157` | O download de arquivos históricos tem repetição com espera linear fixa (1,5 s × tentativa), sem tratar 429 nem `Retry-After`. O cliente REST principal faz isso direito (`rest.py:193-198,248-249`). | Reusar a política do cliente REST (Etapa 4.1). |
| C5 | B | `data/history.py:249` | Cada atualização incremental regrava o Parquet inteiro (o de 1m tem cerca de 31 MB). | Particionar por mês ou acumular antes de gravar. |
| C6 | B | Testes | Sem teste para `data/websocket.py` (reconexão) nem para `engine/live.py`. | Etapa 11.1. |

Já atendido: timeout, repetição com backoff exponencial e variação aleatória, limite de peso, 429 e 418 no cliente REST; checksum SHA-256 dos arquivos históricos; cache em Parquet com manifesto; par ETHBRL direto e câmbio USDTBRL; tudo em UTC com dia de operação em Brasília (`timeutils.py`).

### Confiabilidade do modelo

| # | Pri | Onde | Problema | Como corrigir |
|---|---|---|---|---|
| M1 | A | `engine/decision.py` | **Não há modelo treinado.** São regras fixas com pesos escolhidos à mão (`config.yaml:122-131`). A descrição "modelo adaptativo que só emite ordem firme quando é confiável nos testes" não corresponde ao código atual. | Etapa 8, ou ajustar a descrição do projeto. Ver decisão D2. |
| M2 | A | Resultados (seção 1.2) | Sem vantagem demonstrada: retorno negativo nos dois pares, 13 a 18 operações em 5 meses. Amostra pequena demais para qualquer conclusão estatística. | Não é "corrigível" por código. Manter a apresentação honesta; a regra de confiança (6.4) deve resultar em AGUARDAR enquanto for assim. |
| M3 | A | `cli.py:777-784`, `backtest/costs.py` | **O backtest não é reprodutível.** Ele lê a taxa da conta ao vivo e a tabela de spread de `data/costs.sqlite`, que cresce a cada coleta. Evidência: o relatório salvo dá −3,652% para o ETHBRL padrão e a execução de hoje deu −3,67%; a versão ingênua foi de −76,682% para −76,40%. | Congelar custos num arquivo versionado por execução e gravar no relatório os parâmetros, o hash dos dados e o commit (Etapa 6.5). |
| M4 | M | `engine/decision.py:70-74`, saída do paper | A "confiança" é um produto de fatores, não uma probabilidade, e não é calibrada. No relatório do paper, todos os 277 sinais resolvidos da variante padrão caíram na faixa 0–40%, com 18% de acerto. O painel já avisa que é pontuação (`index.html:70`). | Calibração real na Etapa 8.5. |
| M5 | M | `backtest/runner.py:110-134` | O walk-forward escolhe entre 8 combinações pela média de resultado no treino, com mínimo de 20 operações. Com tão poucas operações, muitos meses ficam sem escolha possível e a seleção é ruidosa. | Relatar o intervalo de confiança da métrica de seleção; considerar grade menor. |
| M6 | M | Auditoria de vazamento | O que confirmei: os pivôs usam janela centrada mas só são liberados com atraso de `k` candles (`indicators/structure.py:29-43`); o simulador só entrega candles fechados antes do instante (`simulator.py:155-160`); existe o teste `test_simulacao_nao_olha_o_futuro`. O que **não** auditei: `season_pit.py`, o percentil móvel de ATR e a tabela de spread por hora (medida no futuro em relação ao período do backtest). | Auditoria completa na Etapa 6.1. A tabela de spread é um vazamento leve conhecido e deve ser documentada. |
| M7 | B | `backtest/metrics.py:69` | Sharpe anualizado com dias sem operação contando como zero. É uma escolha defensável, mas precisa estar explicada no relatório. | Documentar. |

### Simulação e aprendizado

| # | Pri | Onde | Problema | Como corrigir |
|---|---|---|---|---|
| SIM1 | A | `paper/runner.py:53`, `config.yaml:60` | A carteira virtual grava no mesmo arquivo da conta real (`data/agent.sqlite`). A lógica é separada (tabelas `paper_*`, capital vindo de `paper.capital`, a conta real nem é lida no modo paper: `engine/live.py:97-102`), mas o roteiro pede separação total. | Banco próprio para a simulação (Etapa 7.1). |
| SIM2 | A | Projeto todo | Não há ciclo de aprendizado: nada reavalia, retreina, versiona ou promove. Depende de M1. | Etapa 8. |
| SIM3 | M | `paper/` | Não há modo replay com histórico acelerado nem comando de reset. O arquivo `agent_backup_antes_reset_estudo_20261023.sqlite` mostra que o reset foi feito à mão. | Etapa 7.5. |
| SIM4 | M | `paper/book.py:122-127` | A operação simulada guarda confiança e regime, mas não as features, o motivo nem o cenário na própria linha; isso fica em `signals.details_json`, ligado por `signal_id`. | Avaliar se a junção basta ou se convém desnormalizar (Etapa 7.4). |
| SIM5 | M | `backtest/execution.py`, `paper/book.py:114-129` | Não há tamanho mínimo de ordem (as regras de valor mínimo e de lote da Binance não aparecem em nenhum arquivo). Uma ordem virtual de R$ 3 seria aceita. | Ler os filtros do par em `/api/v3/exchangeInfo` (já está na lista permitida) e aplicar. |
| SIM6 | M | `config.yaml:183-184` | Capital virtual e perda máxima vêm do YAML, esta em percentual. O roteiro pede valores em R$ informados pelo usuário. | Etapa 7.1/9.1. |
| SIM7 | B | `paper/report.py` | O relatório do paper não compara com comprar e segurar no mesmo período. | Etapa 7.5. |
| SIM8 | B | Estado atual | 0 operações virtuais fechadas desde 22/10. Qualquer demonstração de aprendizado terá de usar replay. | Previsto no critério da Etapa 8. |

Já atendido: taxa, meio spread, slippage de mercado e de stop, pior caso quando stop e alvo caem no mesmo minuto, abertura com gap (`backtest/execution.py`); as mesmas funções valem para backtest e paper; atraso de execução configurável (`paper.atraso_s`).

### UI/UX

Análise por leitura do código; o painel não foi aberto (seção 1.2).

| # | Pri | Onde | Problema | Como corrigir |
|---|---|---|---|---|
| U1 | M | `style.css:7` | O tema escuro só segue a preferência do sistema; não há botão para alternar. | Botão com preferência salva (Etapa 10.1). |
| U2 | M | `index.html:122-127`, `app.js:167` | A simulação é uma tabela de 5 colunas. Faltam curva de capital, lista de operações, métricas, calibração e versões do modelo. Não há abas. | Etapa 10.2. |
| U3 | M | `index.html` | Não há rótulo nem cor que diferencie SIMULADO de REAL; "Conta e limites" e "Paper trading" têm o mesmo estilo de cartão. | Etapa 10.4. |
| U4 | M | `app.js` | Os sinais não são marcados no gráfico (não há chamada a `setMarkers`); só linhas de preço dos níveis atuais (`app.js:214`). | Marcadores de COMPRAR/VENDER nos candles. |
| U5 | M | `index.html:136` | O aviso legal existe no rodapé, mas com texto diferente do pedido no roteiro e pouco visível. | Texto exato do item 10.6 em destaque. |
| U6 | B | `index.html:8`, `app.js:190` | Sem internet, o gráfico some (há mensagem amigável). | Resolvido junto com S3. |
| U7 | B | `style.css:65` | Só um ponto de quebra responsivo (520 px). | Revisar em telas médias. |
| U8 | B | Raiz | Nenhuma captura de tela no repositório. | Etapa 10.7, em modo demo para não expor saldo. |

Já atendido: erros tratados sem stack trace (`cli.py:1321-1325`, banners em `index.html:21-23`, traceback só no arquivo de log: `logging_setup.py:29`); escape de HTML em tudo que vai para `innerHTML` (`app.js:5`).

### Documentação

| # | Pri | Onde | Problema | Como corrigir |
|---|---|---|---|---|
| DOC1 | A | Raiz | Sem `LICENSE`. Sem licença, ninguém pode legalmente reutilizar o código público. | MIT (Etapa 12.2). |
| DOC2 | M | `README.md` | Bom como manual de uso pessoal, fraco como vitrine: sem imagens, sem diagrama, sem seção em inglês, sem tecnologias, escrito para uma usuária específica ("preencha você mesma", `:36`). | Reescrever na Etapa 12.1, mantendo a honestidade sobre os resultados. |
| DOC3 | M | `README.md:11` | Os números citados (−6,6%, −3,7%) batem com o relatório salvo, mas não com uma execução de hoje (ver M3). | Depois de resolver M3, citar números com data, commit e hash dos dados. |
| DOC4 | B | Raiz | Sem `CHANGELOG.md`, sem `Dockerfile`. | Etapas 12 e 13. |
| DOC5 | B | `pyproject.toml:8` | Sem autor, licença, URLs nem classificadores. | Etapa 12.2. |

---

## 1.5 Plano das etapas 2 a 13, ajustado ao que foi encontrado

Mudança geral: várias tarefas do roteiro já estão prontas. Em cada etapa separo **confirmar** (já existe; só provar e documentar) de **fazer** (trabalho real).

### Decisões que preciso de você antes de avançar

- **D1. Nomes das pastas (Etapa 3.1).** Recomendo **manter** `engine/`, `paper/`, `indicators/` e não renomear para `models/`, `simulation/`, `features/`. Renomear mexe em todos os imports e nos 213 testes sem ganho funcional, e `models/` seria enganoso enquanto não houver modelo treinado. Criaria só o que é novo: `learning/` (Etapa 8) e `cli/` (divisão do arquivo grande).
- **D2. O que é o "modelo adaptativo" (Etapas 6 e 8).** Hoje não existe. Recomendo um modelo simples e explicável (regressão logística sobre as features que o motor já calcula), treinado em walk-forward, que estima a probabilidade de o alvo ser atingido antes do stop **depois dos custos**. As regras atuais viram o "campeão" inicial; o modelo é o primeiro "desafiador". Isso adiciona `scikit-learn` como dependência. É bem possível que o desafiador **não** supere as regras; nesse caso o resultado honesto é o sistema ficar em "AGUARDAR / EM TESTE", e é isso que será mostrado.
- **D3. Chave da Binance.** Está sendo recusada (−2015). Para as etapas 2 a 8 não preciso dela, pois uso dados públicos e modo demo. Para a Etapa 9.3 (ler posição pelo saldo) vou testar com respostas simuladas; a conferência com a conta real fica com você.
- **D4. `ANTHROPIC_API_KEY`.** Recomendo remover do código e do `.env.example`, já que nada a usa.

### Etapa 2 — Segurança e configuração
- Confirmar: segredos só no `.env`; `.gitignore` e `.env.example`; cliente somente leitura; ausência de código de ordens (testes de `test_readonly_guard.py`).
- Fazer: S2 (validar `Host`), S3 (SRI ou biblioteca local), S4 (remover chave não usada, se aprovado), Q6 (números mágicos para o config), Q9 (log estruturado opcional em JSON), Q8 (`.gitattributes`), documentar explicitamente que não há execução real.

### Etapa 3 — Estrutura e qualidade
- Fazer: Q2 (dividir `cli.py`), Q3 (quebrar as 4 funções mais longas, uma por commit, com teste de equivalência), Q4 (docstrings nas funções públicas), anotações que faltam (13 + 11), B6.
- Baseline de comparação (3.4): os 213 testes mais a saída do `backtest --rapido --pares ETHBRL` com custos congelados. Por isso **M3 precisa vir antes**: proponho antecipar o congelamento de custos para o início da Etapa 3.

### Etapa 4 — Coleta de dados
- Confirmar: timeout, backoff, 429/418, cache em Parquet, incremental, ETHBRL, UTC.
- Fazer: C1 (modo demo com amostra versionada de candles públicos), C2 (validação), C3 (alinhamento), C4 (política de repetição no download), C5, teste de falha de rede simulada para o download e o WebSocket.

### Etapa 5 — Performance
- Medir primeiro: inicialização (hoje 1,2 s de import), `dados status` (9,8 s), uma análise ao vivo completa, cálculo de indicadores por timeframe, backtest rápido (32 s) e completo, memória de pico.
- Fazer conforme a medição: P1, P2, P3, P4, P5, P6, P7.

### Etapa 6 — Modelo, backtest e confiabilidade
- Confirmar: walk-forward sem embaralhar; custos; métricas já existentes (retorno, acerto, drawdown, Sharpe, operações, comprar e segurar).
- Fazer: M6 (auditoria completa de vazamento, por escrito), B2, B3, B4, B5, M3 (reprodutibilidade: custos congelados, semente, parâmetros e hash no relatório), M5, limiar de confiança configurável com saída "AGUARDAR" (6.4), primeira versão do modelo de D2, se aprovado.

### Etapa 7 — Simulação
- Confirmar: execução realista compartilhada com o backtest; registro em SQLite.
- Fazer: SIM1 (banco próprio), SIM6 (valores em R$), SIM5 (tamanho mínimo de ordem), B1, SIM4, SIM3 (replay acelerado e reset), SIM7.

### Etapa 8 — Aprendizado contínuo
- Tudo é construção nova: registro de versões do modelo, ciclo de reavaliação, campeão/desafiador com critério fora da amostra depois de custos e sem piorar o drawdown, detecção de mudança de regime, calibração com gráfico, estado "AGUARDAR / EM TESTE".
- A demonstração de "um rejeitado e um promovido" será feita em replay, com os dados reais de cada caso registrados.

### Etapa 9 — Risco
- Confirmar: perguntas do dia (`session/daily.py`), bloqueio por perda máxima no real e no virtual (`account/limits.py`, `paper/book.py:95`), VENDER como saída da posição.
- Fazer: esclarecer 9.3. Hoje a posição vem do **livro de operações do dia limitado ao capital da operação**, não do saldo bruto de ETH (`account/status.py:181-202`), de propósito, para ignorar o ETH que você já tinha. Proponho manter e documentar; se preferir saldo bruto, é uma mudança de regra. Acrescentar o teste do cenário de limite atingido de ponta a ponta.

### Etapa 10 — Dashboard
- Fazer: U1 a U8, aba Simulação, registro manual de operações reais na tela (o comando `diario` já existe no terminal), capturas em modo demo.

### Etapa 11 — Testes, lint e CI
- Fazer: ruff, pre-commit, GitHub Actions, cobertura. Testes novos para os módulos hoje sem teste direto: `data/websocket.py`, `engine/live.py`, `engine/journal.py`, `paper/runner.py`, `backtest/report.py`, `backtest/baseline.py`, `backtest/season_pit.py`, `costs/summary.py`, `account/journal.py`, `storage/agent_db.py`.

### Etapa 12 — Documentação
- Fazer: README novo (S1, DOC2, DOC3), `LICENSE` MIT, trava de dependências, metadados do pacote, instalação do zero em ambiente limpo, varredura final. Dockerfile: faz sentido só para o modo demo; decido quando ele existir.

### Etapa 13 — LinkedIn, changelog, auditoria
- Sem mudança em relação ao roteiro. Só números gerados nas etapas anteriores.

---

## Limites deste diagnóstico

- Li por inteiro: `config.py`, `engine/decision.py`, `engine/rules.py`, `engine/live.py`, `data/rest.py`, `data/history.py`, `data/models.py`, `backtest/simulator.py`, `backtest/runner.py`, `backtest/metrics.py`, `backtest/execution.py`, `backtest/data.py`, `paper/book.py`, `paper/runner.py`, `dashboard/server.py`, `dashboard/service.py`, `dashboard/static/index.html`, `account/permissions.py`, `account/status.py`, `logging_setup.py`, `atalhos/iniciar_agente.ps1`, `README.md`, `config/config.yaml`, `.env.example`, `pyproject.toml`.
- Li em parte (busca por padrão ou trechos): `cli.py`, `app.js`, `style.css`, `indicators/structure.py`, `timeutils.py`.
- **Não li:** `engine/signals.py`, `engine/journal.py`, `engine/context.py`, `indicators/` (exceto `structure.py`), `seasonality/`, `costs/`, `account/ledger.py`, `account/binance_account.py`, `account/limits.py`, `account/journal.py`, `session/daily.py`, `alerts/notify.py`, `data/websocket.py`, `data/health.py`, `data/market_store.py`, `security/redact.py`, `storage/`, `paper/report.py`, `backtest/report.py`, `backtest/baseline.py`, `backtest/costs.py`, `backtest/season_pit.py`, e os arquivos de teste. Podem existir problemas neles que não estão listados; cada um será lido na etapa que o tocar.
- Não rodei lint nem verificador de tipos (não estão instalados, e instalar seria alterar o ambiente numa etapa somente leitura).
- Não abri o painel no navegador.
