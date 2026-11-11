# Agente de decisão cripto (somente leitura)

Assistente que analisa o ETHUSDT/ETHBRL e dá leituras de **COMPRAR / VENDER (sair da posição) / ESPERAR**, com motivos, confiança calculada às claras, níveis de entrada, stop e alvos. **Ele nunca envia ordens.** Você decide e executa.

> ⚠️ Não é recomendação financeira e não há garantia de resultado. Resultados passados não garantem resultados futuros.

## Situação atual (leia antes de usar)

- **As 9 etapas estão prontas:** dados, conta, indicadores, análise por horário, motor de decisão, backtest, paper trading, painel e alertas.
- **A estratégia ainda não tem vantagem comprovada.** No backtest fora da amostra (mai–set/2026, já com taxas e slippage):
  - a versão padrão (setup 15m) **perdeu**: −6,6% no ETHUSDT (18 operações) e −3,7% no ETHBRL (13 operações);
  - a hipótese do setup 1h teve só 2 operações, o que é pouco demais para concluir qualquer coisa;
  - o principal problema é estrutural: no 15m, a volta à média costuma ser **menor que as taxas**.
- **Pendente: o relatório completo de custos ETHUSDT × ETHBRL** (spread e slippage por hora, custo de ida e volta com câmbio nos dois cenários, efeito do câmbio separado, recomendação de par). Ele só faz sentido com pelo menos 7 dias distintos de coleta. Até lá, `custos resumo` mostra o andamento.
- **Por isso, por enquanto, use os sinais só como estudo e acompanhe o paper trading.** Uma conclusão honesta exige pelo menos 30 operações virtuais. Os relatórios completos ficam em `data\reports\`.

## Instalação (Windows, PowerShell)

Requisito: Python 3.11 ou mais novo.

```powershell
cd C:\Users\breno\Documents\IA_para_Criptos_2.0
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\cryptoagent.exe historico baixar
.\.venv\Scripts\cryptoagent.exe sazonalidade analisar
```

**Sempre entre na pasta do projeto antes de rodar qualquer comando** (a cada PowerShell novo): `cd C:\Users\breno\Documents\IA_para_Criptos_2.0`. Depois use `.\.venv\Scripts\cryptoagent.exe ...`. Abaixo, os comandos aparecem abreviados como `cryptoagent ...`.

> Não reinstale o projeto (`pip install -e .`) com o coletor, o paper ou o painel abertos: o Windows trava o `cryptoagent.exe` e a instalação fica pela metade. Feche-os antes.

## Configuração

- **`config/config.yaml`:** tudo o que é ajustável (pares, timeframes, limites de idade dos dados, custos, capital de operação, estratégia, backtest, paper, painel, alertas). Cada campo tem um comentário. Um campo digitado errado gera erro ao iniciar, de propósito.
- **`.env`:** as chaves. Copie `.env.example` para `.env` e preencha você mesma. **Nunca cole as chaves no chat.**
- **Chave da Binance: só "Habilitar leitura".** Se ela tiver qualquer outra permissão (trading, saque, transferência...), o agente se recusa a rodar. Confira com `cryptoagent conta permissoes`.
- **Recomendado: restrição por IP.** Na Binance, em **Perfil → Gerenciamento de API → Editar restrições**, cadastre o seu IP **público IPv4**. Para descobrir qual é: `Invoke-RestMethod https://ipv4.icanhazip.com`. Não use o IP do `ipconfig`. Se o IP de casa mudar, o agente mostra "Falha ao ler a conta" e fica em ESPERAR; basta atualizar o IP na Binance.
- **Relógio do Windows sincronizado.** Uma diferença de mais de 1 s para a Binance já leva a ESPERAR. Se precisar, num PowerShell como **Administrador**: `w32tm /resync /force`.

## Rotina do dia a dia

**Atalhos na área de trabalho** (scripts em `atalhos/`):
- **Agente Cripto - Iniciar:** abre o coletor, o paper trading e o painel, cada um na sua janela. O que já estiver aberto não é duplicado. Ele faz o passo 1 abaixo por você.
- **Agente Cripto - Relatorio:** mostra o `paper relatorio` e o `custos resumo` (passo 5).

1. Abra uma janela para cada processo que deve ficar rodando. Pode minimizar, mas não feche:
   - `cryptoagent custos coletar`: mede spread e slippage ao vivo (mínimo 7 dias distintos, ideal 14);
   - `cryptoagent paper rodar`: carteira virtual seguindo os sinais;
   - `cryptoagent painel`: abre `http://127.0.0.1:8765` no navegador.
2. No painel, responda as **perguntas do dia** (capital e perda máxima). Sem isso, o agente fica em ESPERAR.
3. Acompanhe a decisão, os motivos e os alertas. **Se for comprar, coloque a ordem de stop (ou OCO) na Binance logo em seguida.**
4. Confira no painel a associação da sua compra com o sinal: confirme ou corrija.
5. De vez em quando, rode `cryptoagent paper relatorio` e `cryptoagent custos resumo`.
6. Uma vez por semana, rode `cryptoagent historico baixar` e `cryptoagent sazonalidade analisar` para atualizar o histórico e o mapa de calor.

Pode fechar tudo com **Ctrl+C** a qualquer hora: os dados ficam salvos e cada processo continua de onde parou quando for aberto de novo.

## Todos os comandos

| Grupo | Comando | O que faz |
|---|---|---|
| Dados | `dados status` | Foto dos dados agora: preços, candles, derivativos, relógio, saúde |
| | `dados ao-vivo --segundos 60` | Preço e candles pelo WebSocket no terminal |
| Histórico | `historico baixar` / `historico verificar` | Baixa ou atualiza 13 meses de candles (Parquet) e mostra os buracos |
| Custos | `custos coletar` / `custos resumo` | Coleta spread, profundidade e slippage ao vivo e resume a coleta |
| Sessão | `sessao iniciar` / `sessao ver` | Perguntas do dia (capital e perda máxima, par, saldo) |
| Conta | `conta permissoes` | Confere se a chave é somente leitura |
| | `conta status` | Saldo, taxas reais, resultado do dia, limites |
| Diário | `diario adicionar/listar/remover` | Contingência sem chave (com `conta.modo: diario_manual`) |
| Indicadores | `indicadores ver --tf 15m` / `indicadores validar` | Valores ao vivo e validação no histórico |
| Horário | `sazonalidade analisar` / `sazonalidade agora` | Mapa de calor, validação, comparação de agora com o histórico |
| Decisão | `analisar` | COMPRAR/VENDER/ESPERAR agora, com tudo explicado |
| Sinais | `sinais listar` / `sinais associar --par ETHBRL --operacao 123 --sinal 7` | Histórico de sinais e associação com as compras |
| Backtest | `backtest` (`--pares`, `--rapido`) | Walk-forward, ablação, comparações; relatório em `data\reports\backtest.html` |
| Paper | `paper rodar` / `paper relatorio` | Carteira virtual e "o que o mercado fez" depois de cada sinal |
| Painel | `painel` (`--porta`, `--sem-navegador`) | Painel ao vivo + alertas |
| Alertas | `alertas testar` | Alerta de teste no terminal e no Windows |
| Hipóteses | `--sobrepor arquivo.yaml <comando>` | Roda qualquer comando trocando só os campos do arquivo |

## Como o agente decide (reversão à média intraday, só spot)

Os sinais são calculados no ETHUSDT, que é mais líquido, e os preços são traduzidos para o par em que você opera.

**1. Travas.** Qualquer uma leva a ESPERAR, com o motivo escrito:
- dados frescos e completos, relógio certo, sessão do dia respondida, saldo lido;
- perda máxima do dia não atingida. Com ela atingida e posição aberta, só aparece VENDER em stop, invalidação ou tempo;
- spread abaixo do limite e incerteza baixa;
- regime do 15m diferente de alta volatilidade;
- sem queda forte no 1h e sem queda sustentada no 4h;
- **gatilho anti-faca caindo:** o preço esticou e voltou para dentro da banda, subindo;
- alvo a pelo menos 2,5× o custo de ida e volta;
- risco/retorno de pelo menos 1 depois das taxas.

**2. Sinais (conjunto mínimo):** distância ao VWAP do dia, %B das Bandas de Bollinger e RSI, cada um com nota de 0 a 1. Os extras (Stoch RSI, divergência, pavio, fluxo taker, suporte, order book) estão desligados. Na ablação, nenhum deles deixou a estratégia positiva.

**3. Confiança** = nota dos sinais × confirmação do 1h/4h × qualidade dos dados × fator do horário. Só os padrões de horário validados fora da amostra entram, e eles só reduzem a confiança. É uma pontuação, não uma probabilidade. O mínimo depende do regime: 50% em tendência de alta, 55% lateral, 60% transição, 70% tendência de baixa.

**4. Níveis:**
- **Stop obrigatório** abaixo do fundo recente.
- **Alvo 1** na média (VWAP ou meio das bandas), ou numa resistência que esteja antes dela.
- **Alvo 2** na banda superior.
- **Tamanho** calculado para que, se o stop for atingido, a perda seja o risco por operação (perda máxima do dia ÷ 3).

**5. VENDER (com posição):** stop atingido, alvo atingido, preço esticado para cima, leitura invalidada por queda forte, ou 3 h sem chegar ao alvo.

## Resultado do dia e limites

- **O que entra no resultado:** só as operações executadas no par da operação, até o teto do `capital_operacao`. Depósitos, saques e o ETH que você já tinha ficam fora.
- **Posição vinda de ontem:** é reavaliada pelo preço das 00:00 de Brasília, então o resultado de ontem não consome o limite de hoje.
- **Lucro do dia não aumenta a perda permitida.** A perda máxima pode ser reduzida no meio do dia, mas não aumentada.

## Backtest e validação (resumo)

- **Mesmo motor do modo ao vivo:** só usa candles fechados antes de cada decisão. Um teste garante que cortar o futuro não muda o passado.
- **Execução no par real, minuto a minuto:**
  - entrada a mercado com meio spread, slippage e taxa;
  - OCO simulada com stop e alvo, e se os dois caem no mesmo minuto, conta o stop (pior caso);
  - o stop escorrega mais que a ordem a mercado.
- **Walk-forward:** aprende em 6 meses e mede no mês seguinte, numa grade pequena declarada antes de ver os resultados.
- **O relatório também mostra:** trava de distância mais baixa, ablação, versão ingênua, comprar e segurar e os motivos de ESPERAR. **Inclui todos os resultados ruins.**
- **Hipótese 1h:** registrada em `config/hipotese_setup_1h.yaml` antes de rodar. Confirmou que os movimentos no 1h são maiores que as taxas, mas não demonstrou lucro (2 operações).
- **Variante de estudo (`config/estudo_travas.yaml`, só no paper trading):** a carteira virtual compra mesmo quando só as travas de **previsão** seguraram (tendência, regime, preço não esticado, gatilho, confiança). As travas de **segurança** continuam valendo (dados ruins, spread alto, perda máxima do dia virtual, stop obrigatório), e as de **custo** também (alvo perto demais, risco/retorno). Em 23/10, quando as de custo ainda eram ignoradas, foram 16 operações e 16 prejuízos: os alvos não pagavam as taxas. Cada compra guarda quais travas ignorou, e o `paper relatorio` compara as operações que cada trava teria barrado com as que deixaria passar. Os sinais do painel e do `analisar` **não mudam**. Mexer numa trava exige 30 ou mais operações de cada lado e uma hipótese declarada testada no backtest.
- **Limitações:**
  - o order book não tem histórico, então só é avaliado no paper trading;
  - o spread por hora vem da coleta ao vivo, que ainda cobre poucas horas;
  - a amostra de operações é pequena.

## Segurança

- **Só leitura:** o cliente da Binance só faz consultas GET, a uma lista fixa de endpoints de leitura. Testes varrem o código procurando chamadas de ordem, saque ou transferência.
- **Não existe código de execução real.** Não há função, flag nem configuração que envie ordem: o programa só lê dados, gera sinais e simula com dinheiro virtual. Se um dia isso for acrescentado, a regra do projeto é ficar desligado por padrão, atrás de uma flag explícita e com aviso claro.
- **Chave verificada:** as permissões são conferidas ao iniciar e a cada hora. Com qualquer permissão além de leitura, o agente para.
- **Segredos:** as chaves só vêm do `.env`. Logs, mensagens de erro e a página do painel passam por um filtro que mascara chaves e assinaturas.
- **Painel:** só escuta no próprio computador (`127.0.0.1`) e recusa chamadas cujo cabeçalho `Host` não seja `127.0.0.1` ou `localhost`. O que altera algo exige um token que só a página do painel tem.
- **Dado velho, ausente ou inconsistente = ESPERAR**, sem presumir valores.

## Horários

Tudo é guardado em UTC. O dia de operação segue o horário de Brasília e começa às 00:00. O candle diário da Binance vai de 21:00 a 21:00 em Brasília, por isso não é usado como "dia de hoje".

## Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| "O termo cryptoagent.exe não é reconhecido" | PowerShell fora da pasta do projeto | `cd C:\Users\breno\Documents\IA_para_Criptos_2.0` |
| Erro `-2015` / "Falha ao ler a conta" | IP mudou ou chave errada no `.env` | Atualize o IP na Binance; confira o `.env` sem espaços nem aspas |
| "relógio do PC ... sincronize" | Relógio do Windows fora de sincronia | `w32tm /resync /force` (como Administrador) |
| "RECUSADO: permissões além de leitura" | A chave tem trading/saque etc. | Deixe só "Habilitar leitura" na Binance |
| Painel mostra "API REST (fallback)" | WebSocket caiu ou está reconectando | Normal por alguns segundos; ele volta sozinho |
| Erro de porta ao abrir o painel | Outro painel já aberto | Feche a outra janela ou use `painel --porta 8766` |

## Estrutura do projeto

```
config/            config.yaml (tudo ajustável), hipotese_setup_1h.yaml e estudo_travas.yaml
src/cryptoagent/
  data/            REST e WebSocket somente leitura, histórico, saúde dos dados
  account/         permissões, saldo, operações, resultado do dia, limites, diário manual
  session/         perguntas do início do dia
  indicators/      EMA, RSI, MACD, Stoch RSI, ATR, Bollinger, ADX, VWAP, estrutura, regime
  seasonality/     análise por horário e validação walk-forward
  costs/           coletor de custos, slippage, custo de ida e volta
  engine/          motor de decisão (sinais, travas, níveis, decisão, registro)
  backtest/        simulador, execução, métricas, walk-forward, relatório
  paper/           carteira virtual e relatório
  dashboard/       serviço ao vivo, servidor local e página
  alerts/          alertas (terminal, Windows, painel)
tests/             testes automáticos
data/  logs/       dados locais e logs (fora do git)
```

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Os testes cobrem: indicadores (contra referências publicadas e cálculos à mão), motor de decisão (cada regra), backtest (sem olhar o futuro, atalhos exatos, custos, métricas), conta e limites, fuso horário, segurança (somente leitura, segredos, token do painel) e paper trading.
