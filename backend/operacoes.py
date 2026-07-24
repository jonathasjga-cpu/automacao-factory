"""Dicionario global de operacoes em andamento.

Isolado num modulo proprio pra evitar duplicacao quando `main` eh importado
por caminhos diferentes (uvicorn vs `from main import`). Sem esse cuidado,
Python cria 2 instancias do dict — /api/executar popula uma, o
_injetor_resultado_em_operacao le a outra, e nada bate.
"""

status_operacoes: dict = {}
