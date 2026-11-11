"""Motor de decisão: regras explícitas e auditáveis (nada de caixa-preta).

O MESMO código decide ao vivo e no backtest: os dois montam um DecisionContext e
chamam decide(). Assim o backtest testa exatamente o que você vai usar.
"""
