"""Conversas simuladas com o modelo de verdade. Cada cenário roda várias vezes, porque o LLM não responde sempre igual.

Uso: python cenarios.py [rodadas]   (padrão: 3)
"""

import sys

from langchain_core.messages import HumanMessage

from bot import MODELO, construir_grafo

MARIA = {"nome": "Maria Souza", "email": "maria@email.com", "telefone": "(11) 98765-4321"}


def recebeu_proposta(estado: dict, carrinho: dict) -> bool:
    proposta = estado.get("proposta") or {}
    return proposta.get("carrinho") == carrinho and proposta.get("cliente") == MARIA


def pediu_o_celular(estado: dict) -> bool:
    ultima = estado["messages"][-1].text.lower()
    return "telefone" not in estado.get("cliente", {}) and ("celular" in ultima or "telefone" in ultima)


# Nome do cenário: (mensagens do cliente, em ordem; verificação do estado no fim da conversa).
CENARIOS = {
    "Conversa mínima do desafio": (
        ["Olá, estou interessado em comprar um smartphone.", "Eu queria o NovoPhone X12.",
         "Claro, meu nome é Maria Souza.", "Claro, é maria@email.com e (11) 98765-4321."],
        lambda e: recebeu_proposta(e, {"novophone-x12": 1}),
    ),
    "Todos os dados numa mensagem, fora de ordem": (
        ["Quero comprar o TimeWatch S2.",
         "Meu e-mail é maria@email.com, meu celular é (11) 98765-4321 e meu nome é Maria Souza."],
        lambda e: recebeu_proposta(e, {"timewatch-s2": 1}),
    ),
    "Só o e-mail: o bot pede o celular": (
        ["Quero comprar o SoundBuds Plus.", "Meu nome é Maria Souza.", "maria@email.com"],
        pediu_o_celular,
    ),
    "E-mail inválido e depois corrigido": (
        ["Quero comprar o TabMaster 10.", "Meu nome é Maria Souza.", "maria.email.com e (11) 98765-4321",
         "Desculpa, o certo é maria@email.com"],
        lambda e: recebeu_proposta(e, {"tabmaster-10": 1}),
    ),
    "Troca de produto": (
        ["Quero comprar o TabMaster 10.", "Na verdade, troca pelo GameBox X.", "Meu nome é Maria Souza.",
         "maria@email.com e (11) 98765-4321"],
        lambda e: recebeu_proposta(e, {"gamebox-x": 1}),
    ),
    "Acima do CAP: transfere para um humano": (
        ["Quero comprar o UltraBook Pro 15."],
        lambda e: e.get("handoff") and not e.get("proposta"),
    ),
}

if __name__ == "__main__":
    rodadas = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    grafo = construir_grafo(banco=":memory:")  # o mesmo bot do app, sem gravar no conversas.db
    print(f"Modelo: {MODELO}, {rodadas} rodadas por cenário")
    for nome, (mensagens, passou) in CENARIOS.items():
        acertos = 0
        for i in range(rodadas):
            config = {"configurable": {"thread_id": f"{nome}-{i}"}}
            for mensagem in mensagens:
                estado = grafo.invoke({"messages": [HumanMessage(mensagem)]}, config)
            acertos += bool(passou(estado))
        print(f"{acertos}/{rodadas}  {nome}")
