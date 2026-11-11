# Changelog

Formato inspirado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). Os números vêm de medições reais registradas no [PROGRESSO.md](PROGRESSO.md), que tem a evidência de cada item.

## [Não lançado] — Revisão completa (10 e 11/11/2026, branch `melhorias`)

Revisão em 14 etapas (0 a 13), uma por vez, com aprovação a cada etapa: diagnóstico, segurança, estrutura, coleta, desempenho, modelo e backtest, simulação, aprendizado contínuo, risco, painel, testes e CI, documentação e auditoria final. Mais de 60 commits sobre o estado inicial (`8ab7665`), cada etapa com uma tag de backup (`backup-antes-etapa-N`).

### Antes e depois

| Aspecto | Antes | Depois |
|---|---|---|
| Testes automáticos | 213 | 381 |
| Cobertura | não medida | 79% do pacote; 89,9% a 98,8% nas 9 áreas críticas |
| Lint, formatação, CI | nenhum | ruff (estilo black), pre-commit, GitHub Actions (Ubuntu e Windows) |
| Complexidade das funções | `simulate` 33, `decide` 24 (sem medição contínua) | no máximo 15, com 4 exceções justificadas no código |
| `cryptoagent --help` | 1,26 s | 0,27 s |
| Backtest rápido | 33,8 s | 10,5 s |
| Backtest completo (grade + ablação) | 250 s | 67 s |
| Reprodutibilidade do backtest | dependia da taxa lida da conta e de um banco de custos que cresce | custos congelados em arquivo; procedência gravada (commit, parâmetros, hash de cada arquivo de dados, semente) |
| Walk-forward | operava mesmo quando todas as combinações perdiam no treino (13 operações, −4,21%) | não opera nesses meses (0 operações no período testado) |
| Comprar e segurar | taxa fixa de 0,1% (+24,07%) | os mesmos custos de uma operação do backtest (+24,02%) |
| "Modelo" | só regras fixas | campeão (regras) x desafiantes, incluindo regressão logística (AUC 0,486 fora da amostra; não promovida) |
| Confiança na tela | pontuação apresentada sem calibração | calibração medida e exibida; compra só vira "firme" com backtest validado; hoje aparece "AGUARDAR (estratégia em teste)" |
| Carteira virtual | % do capital, no mesmo banco da conta | R$ fixos, banco próprio, regras de tamanho de ordem da Binance, atraso de execução, replay do histórico |
| Modo demonstração | não existia | sem chave e sem internet: backtest, modelo, aprendizado, replay e painel |
| Painel | uma página, gráfico vindo de CDN sem verificação | abas Agora / Simulação / Operações reais, tema claro/escuro, rótulos REAL x SIMULADO, biblioteca servida localmente com hash conferido |
| Pacote de coleta no git | **ausente**: o `.gitignore` escondia `src/cryptoagent/data/` | versionado |
| Documentação | README com caminhos pessoais e resultados de outra versão | README com números medidos nesta versão, resumo em inglês, licença MIT, versões travadas |

### Corrigido

- **`.gitignore` escondia o pacote de coleta** (`src/cryptoagent/data/`, 7 arquivos): o repositório público sairia sem o módulo de dados.
- **Walk-forward escolhia a combinação "menos ruim"** mesmo quando todas perdiam no treino.
- **Comprar e segurar** usava taxa fixa em vez dos custos do backtest.
- **Sharpe** contava os dias em UTC e descartava operações da véspera local.
- **Notificação do Windows** herdava as chaves da Binance no ambiente do processo filho.
- **Painel** respondia a qualquer cabeçalho `Host` (risco de DNS rebinding); agora só `127.0.0.1`/`localhost`.
- **Download do histórico** repetia com espera linear, sem tratar 429/418; agora usa a mesma política de repetição do cliente REST e grava o que já chegou se a rede cair.
- **Proteção de idade do saldo** (`conta.saldo_max_s`) estava no config, mas nenhum código a aplicava.
- **OCO da carteira virtual:** conferia também o minuto da compra, incluindo os segundos antes da entrada, o que podia disparar stop ou alvo que não teriam acontecido; agora só confere minutos que começaram depois da execução.
- **Testes do modo demo** gravavam modelos na pasta do projeto a cada execução.
- **Conexões SQLite** abertas e nunca fechadas em 5 arquivos de teste.
- **APIs do pandas** marcadas como obsoletas no pandas 3.1 (`dt.weekday`, `join(rsuffix=...)`).
- **Biblioteca do gráfico** ficava fora do pacote instalável (não editável).

### Adicionado

- Validação de candles (ausentes, duplicados, fora de ordem, fora da grade, incoerentes, outliers, lacunas) no download, na análise ao vivo e no backtest.
- Política única de repetição com espera exponencial, sorteio e `Retry-After`.
- Modo `--demo` com amostra pública de candles do ETH/BRL (jun–out/2026).
- Cache de indicadores e coleta em paralelo na análise ao vivo; importação preguiçosa dos comandos.
- Auditoria de "olhar o futuro" do backtest ([docs/AUDITORIA_BACKTEST.md](docs/AUDITORIA_BACKTEST.md)) com testes de truncamento e alteração.
- Procedência do backtest e do modelo (`*.meta.json`).
- Modelo desafiador (regressão logística, salvo em JSON, semente fixa).
- Regra de confiabilidade: compra firme só com backtest recente, com os mesmos parâmetros, ≥ 30 operações, média e intervalo de 95% acima de zero.
- Carteira virtual em R$ com banco próprio, filtros de ordem reais da Binance, replay acelerado e reset com cópia de segurança.
- Aprendizado contínuo: registro de versões, critérios de promoção, ciclo ponto no tempo, alerta de mudança de regime (PSI com correção para amostra pequena), panoramas por cenário com intervalo de Wilson.
- Risco: posição da operação conferida com o saldo de ETH da conta; testes de ponta a ponta da sessão do dia e da perda máxima.
- Painel: abas, tema, marcadores de sinais no gráfico, curva de capital, registro manual de operações reais, aviso legal fixo, erros amigáveis.
- Testes de WebSocket (servidor local), walk-forward (simulador falso), registro de operações, relatório da carteira virtual e sinais extras.
- `ruff`, `pre-commit` (inclui trava contra `.env`, bancos, `data/`, `logs/` e chaves privadas), GitHub Actions, relatório de cobertura por área ([docs/COBERTURA.md](docs/COBERTURA.md)).
- `LICENSE` (MIT), `requirements.txt` e `requirements-dev.txt`, README novo, `LINKEDIN.md`.

### Alterado

- `cli.py` (1.330 linhas) virou o pacote `cli/` com 15 módulos.
- `simulate`, `decide`, `run_pair`, `analyze_live` e `AccountMonitor.refresh` divididos em funções menores; o backtest de referência saiu idêntico byte a byte depois de cada divisão (SHA-256 `ac4ae271…` no rápido e `111d501b…` no completo).
- Type hints em todas as funções e docstrings nos itens públicos.
- Números fixos no código (repetição, limites de candles, penalidades) foram para o `config.yaml`, com os mesmos valores.
- Log em JSON opcional (`logs.formato: json`), com segredos mascarados.

### Removido

- `ANTHROPIC_API_KEY` do config e do `.env.example` (não era usada).
- Código sem uso confirmado: `engine/journal.last_signal`, `SymbolAnalysis.hour_row`, `RiskView.quote_to_brl`.

### Resultado da estratégia (inalterado pela revisão, agora medido com rigor)

ETHBRL, fora da amostra de 01/05 a 30/09/2026, com custos: **−3,67% em 13 operações**, contra **+24,02%** de comprar e segurar. Nenhuma combinação de parâmetros ganhou no treino do walk-forward. **A estratégia não tem vantagem comprovada**, e o projeto mostra isso no README, no relatório e no painel.

### Pendências conhecidas

- O CI ainda não rodou no GitHub (o repositório não tem remote); Linux e Python 3.11 não foram testados.
- A chave da Binance do autor está recusada (−2015); os caminhos da conta real foram testados só com respostas simuladas.
- Decisões em aberto: renomear ESPERAR para AGUARDAR em todo o projeto; copiar o histórico antigo do paper trading para o banco novo.
