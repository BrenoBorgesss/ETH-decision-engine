# Textos para o LinkedIn

Todos os números abaixo foram medidos no projeto (ver [README](README.md), [CHANGELOG](CHANGELOG.md) e [PROGRESSO](PROGRESSO.md)). Repositório: https://github.com/BrenoBorgesss/eth-decision-engine

---

## Versão curta (para o feed)

Construí um agente que analisa ETH em reais na Binance e diz COMPRAR, VENDER ou ESPERAR, explicando cada decisão. Ele só lê dados: nunca envia ordens.

O resultado mais importante foi um "não": no teste fora da amostra (mai–set/2026, com taxas e spread), a estratégia perdeu 3,67% em 13 operações, enquanto comprar e segurar ganhou 24,02%. Em vez de ajustar até o número ficar bonito, o projeto mostra isso no relatório e no painel.

O que aprendi construindo: backtest walk-forward, auditoria contra "olhar o futuro", carteira virtual em R$, campeão x desafiante com um modelo de ML (que também não superou as regras), e 381 testes automatizados.

Código aberto: https://github.com/BrenoBorgesss/eth-decision-engine

Ferramenta educacional, não é recomendação financeira.

#Python #DataScience #MachineLearning #Backtesting #Cripto

---

## Versão média

**O que um projeto de trading me ensinou sobre dados: medir é mais importante que acertar.**

Desenvolvi um agente de decisão para ETH/BRL na Binance. A cada 5 minutos ele lê o mercado e sugere COMPRAR, VENDER (sair da posição) ou ESPERAR, com os motivos, uma confiança calculada às claras e os níveis de stop e alvo. Ele é somente leitura: não existe código que envie ordem.

O que está por trás:
• Coleta com validação de dados (buracos, duplicados, preços fora da escala) e repetição com espera exponencial
• Backtest com o mesmo motor do modo ao vivo, execução minuto a minuto, taxas, spread e slippage
• Walk-forward mês a mês e testes que garantem que o backtest não olha o futuro
• Carteira virtual em reais, com as regras reais de tamanho de ordem, e replay acelerado do histórico
• Aprendizado contínuo: as regras (campeão) contra desafiantes, incluindo uma regressão logística, só com dados do passado
• Painel local com abas, tema claro/escuro e separação visível entre REAL e SIMULADO

Os resultados, sem maquiagem (ETHBRL, fora da amostra, mai–set/2026):
• Regras: −3,67% em 13 operações; comprar e segurar: +24,02%
• Walk-forward: nenhuma combinação de parâmetros ganhou no treino, então ele não operou
• Modelo de ML: AUC 0,486 (um sorteio dá 0,5)
• Motivo principal: no 15m, só 37,9% dos sinais tinham alvo maior que o custo de ida e volta

Também revisei a engenharia: backtest completo de 250 s para 67 s, 213 para 381 testes, cobertura de 89,9% a 98,8% nas partes críticas, lint, pre-commit e CI.

Para mim, o maior ganho foi construir um sistema que impede a estratégia de parecer melhor do que é.

Código e documentação: https://github.com/BrenoBorgesss/eth-decision-engine

Ferramenta educacional, não é recomendação financeira. Resultados simulados não garantem resultados reais.

#Python #DataScience #MachineLearning #Backtesting #QuantitativeFinance #Pandas #FastAPI

---

## Versão técnica

**Agente de decisão cripto, somente leitura: arquitetura, validação e resultados.**

Stack: Python 3.11+, pandas/NumPy/PyArrow, httpx + websockets (cliente Binance assíncrono, só GET numa lista fixa de endpoints), pydantic (config validada), scikit-learn, FastAPI + uvicorn (painel em 127.0.0.1), SQLite, pytest, ruff, pre-commit, GitHub Actions.

Decisões de engenharia:
1. **Um único motor de decisão** usado no modo ao vivo, no backtest e na carteira virtual. No backtest, leitores por arrays do NumPy no lugar de `iloc` reduziram a simulação de 31,2 s para 7,9 s, com o relatório idêntico byte a byte (SHA-256 conferido a cada mudança).
2. **Sem olhar o futuro:** testes que cortam ou alteram os dados depois de cada instante e conferem que a decisão não muda; carteira virtual que só confere stop/alvo em minutos que começaram depois da execução.
3. **Reprodutibilidade:** custos congelados em arquivo e procedência gravada com cada relatório (commit, parâmetros e o hash deles, hash de cada arquivo de dados, semente).
4. **Walk-forward honesto:** grade pequena declarada antes; mês sem combinação lucrativa no treino não opera. Antes da revisão, o algoritmo escolhia a combinação "menos ruim" e operava assim mesmo (−4,21%).
5. **Campeão x desafiante, ponto no tempo:** promoção só por critérios declarados (≥ 30 operações e 60 dias, média positiva e 0,05 ponto acima da do campeão, drawdown que não piora, nenhuma mudança de regime detectada); drift por PSI com correção para amostra pequena (o primeiro desenho alertava em 6 de 6 ciclos só por ruído; o final, em 1 de 6).
6. **Confiança tratada como pontuação:** a calibração mostrou acerto de 20% a 34% em todas as faixas, então o painel não a apresenta como probabilidade e mantém o aviso "estratégia em teste".

Resultados (ETHBRL, fora da amostra 01/05–30/09/2026, custo médio de ida e volta 0,26%):
• Regras: 13 operações, −3,67%, acerto 23,1%, média por operação −0,28% (IC 95%: −0,63% a +0,07%)
• Comprar e segurar com os mesmos custos: +24,02%
• Versão ingênua (sem filtros): 598 operações, −76,40%
• Regressão logística: AUC 0,486, Brier 0,256 (referência 0,224)

Qualidade: 381 testes, 79% de cobertura do pacote e 89,9% a 98,8% nas 9 áreas críticas, complexidade ciclomática máxima de 15 (4 exceções justificadas), varredura de segredos em todo o histórico do git.

Conclusão: a estratégia de reversão à média no 15m não paga os custos neste período, e o projeto está construído para mostrar isso. O próximo passo é testar um timeframe maior, hipótese que já está registrada antes de rodar.

Repositório: https://github.com/BrenoBorgesss/eth-decision-engine

Ferramenta educacional, não é recomendação financeira. Resultados simulados não garantem resultados reais.

#Python #MachineLearning #Backtesting #DataEngineering #QuantitativeFinance #FastAPI #SoftwareEngineering

---

## Seção "Projetos" do perfil

**Nome:** Agente de decisão cripto (ETH/BRL, somente leitura)

**Descrição (até ~2.000 caracteres):**

Assistente em Python que analisa ETH em reais na Binance e sugere COMPRAR, VENDER (sair da posição) ou ESPERAR a cada 5 minutos, com os motivos, a confiança calculada às claras e os níveis de stop e alvo. Somente leitura: não envia ordens.

• Coleta por REST e WebSocket com validação de dados e repetição com espera exponencial
• Backtest com o mesmo motor do modo ao vivo, execução minuto a minuto com taxas, spread e slippage, walk-forward mensal, ablação dos sinais e auditoria contra "olhar o futuro"
• Carteira virtual em R$ com as regras de ordem da Binance e replay acelerado do histórico
• Aprendizado contínuo: regras (campeão) x desafiantes, incluindo regressão logística, avaliados só com dados do passado
• Painel local (FastAPI) com abas, tema claro/escuro e separação entre REAL e SIMULADO; modo demonstração sem chave e sem internet
• 381 testes, cobertura de 89,9% a 98,8% nas partes críticas, ruff, pre-commit e GitHub Actions

Resultado honesto: fora da amostra (mai–set/2026), a estratégia perdeu 3,67% em 13 operações contra +24,02% de comprar e segurar; o modelo de ML teve AUC 0,486. O projeto mostra isso no relatório e no painel.

**Competências:** Python · pandas · NumPy · scikit-learn · FastAPI · SQLite · Backtesting · Validação walk-forward · Testes automatizados · CI/CD

---

## Palavras-chave

Python, ciência de dados, machine learning, regressão logística, backtesting, validação walk-forward, look-ahead bias, séries temporais, análise técnica, reversão à média, criptomoedas, Ethereum, Binance API, WebSocket, pandas, NumPy, scikit-learn, FastAPI, SQLite, pytest, cobertura de testes, GitHub Actions, pre-commit, ruff, engenharia de dados, reprodutibilidade, paper trading, gestão de risco.
