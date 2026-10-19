"""Assistente de vendas: catálogo, tools e máquina de estados (LangGraph)."""

import json
import os
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import InjectedState, ToolNode, tools_condition
from langgraph.types import Command

from moderacao import contem_palavrao, criar_moderador
from prompts import PROMPT

load_dotenv()

MODELO = os.getenv("MODELO", "qwen3:8b")  # modelo do Ollama, ou um id "claude-*" para usar a API da Anthropic
CAP = 5_000.00  # acima deste total, a venda vai para um vendedor humano
BANCO = Path(__file__).with_name("conversas.db")  # histórico e estado de cada conversa

# Os destaques são fictícios e são a única fonte de características que o bot pode citar.
CATALOGO = {
    "novophone-x12": {"nome": "NovoPhone X12", "categoria": "Smartphone", "preco": 3499.00,
                      "destaques": "câmera excelente e bateria que dura o dia todo"},
    "ultrabook-pro-15": {"nome": "UltraBook Pro 15", "categoria": "Laptop", "preco": 7999.00,
                         "destaques": "alto desempenho, bom para edição de vídeo, tela de 15 polegadas"},
    "timewatch-s2": {"nome": "TimeWatch S2", "categoria": "Smartwatch", "preco": 1299.00,
                     "destaques": "elegante, resistente à água, acompanha atividades físicas"},
    "tabmaster-10": {"nome": "TabMaster 10", "categoria": "Tablet", "preco": 2499.00,
                     "destaques": "tela de 10 polegadas, leve, bom para estudo e entretenimento"},
    "soundbuds-plus": {"nome": "SoundBuds Plus", "categoria": "Fone de ouvido", "preco": 399.00,
                       "destaques": "sem fio, com estojo de recarga"},
    "photosnap-dslr": {"nome": "PhotoSnap DSLR", "categoria": "Câmera", "preco": 4499.00,
                       "destaques": "câmera DSLR para fotos com qualidade profissional"},
    "visionscreen-55": {"nome": 'VisionScreen 55" 4K', "categoria": "Smart TV", "preco": 3999.00,
                        "destaques": "tela de 55 polegadas, resolução 4K, apps de streaming"},
    "gamebox-x": {"nome": "GameBox X", "categoria": "Console de videogame", "preco": 2999.00,
                  "destaques": "console para jogos em alta resolução"},
}
CATEGORIAS = [p["categoria"] for p in CATALOGO.values()]
CAMPOS_CLIENTE = {"nome": "nome", "email": "e-mail", "telefone": "telefone"}
ETAPAS = {
    "escolher_produto": "Escolha do produto",
    "coletar_dados": "Dados do cliente",
    "gerar_proposta": "Gerando proposta",
    "concluido": "Proposta enviada",
    "handoff": "Transferido para especialista",
}
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def brl(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def total(carrinho: dict[str, int]) -> float:
    return sum(CATALOGO[pid]["preco"] * qtd for pid, qtd in carrinho.items())


def _mesclar(atual: dict, novo: dict) -> dict:
    """Reducer de atualizações parciais: quantidade 0 remove o item do carrinho."""
    return {k: v for k, v in {**atual, **novo}.items() if v}


class Estado(MessagesState):
    carrinho: Annotated[dict[str, int], _mesclar]  # id do produto -> quantidade
    cliente: Annotated[dict[str, str], _mesclar]   # nome, email, telefone
    proposta: dict                                 # pedido no momento em que a proposta saiu
    handoff: bool


def etapa(estado: dict) -> str:
    """Etapa atual, derivada só do estado: o LLM nunca decide que uma etapa terminou."""
    carrinho, cliente = estado.get("carrinho", {}), estado.get("cliente", {})
    if total(carrinho) > CAP:
        return "handoff"
    if not carrinho:
        return "escolher_produto"
    if CAMPOS_CLIENTE.keys() - cliente.keys():
        return "coletar_dados"
    proposta = estado.get("proposta") or {}
    if (proposta.get("carrinho"), proposta.get("cliente")) != (carrinho, cliente):
        return "gerar_proposta"
    return "concluido"


def _normalizar(texto: str) -> str:
    return re.sub(r"[^a-z0-9]", "", texto.lower())


def achar_produto(produto: str) -> str | None:
    """Id a partir do id ou do nome, mesmo parcial ("photosnap"); None se nenhum ou mais de um produto bater."""
    alvo = _normalizar(produto)
    achados = [pid for pid, p in CATALOGO.items() if alvo and (alvo in _normalizar(p["nome"]) or alvo == _normalizar(pid))]
    return achados[0] if len(achados) == 1 else None


def formatar_telefone(telefone: str) -> str | None:
    """Telefone brasileiro com DDD (com ou sem +55), formatado; None se inválido."""
    digitos = re.sub(r"\D", "", telefone)
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    if len(digitos) not in (10, 11) or digitos[0] == "0":
        return None
    return f"({digitos[:2]}) {digitos[2:-4]}-{digitos[-4:]}"


def cliente_digitou(valor: str, mensagens: list, so_digitos: bool = False) -> bool:
    """O valor aparece em alguma mensagem do cliente? Barra dado que o LLM preencheu sozinho, como um telefone de exemplo
    quando o cliente só passou o e-mail. `so_digitos` compara só os números (telefone com ou sem +55, espaço ou traço)."""
    limpar = (lambda t: re.sub(r"\D", "", t)) if so_digitos else (lambda t: " ".join(t.casefold().split()))
    return any(limpar(valor) in limpar(m.text) for m in mensagens if m.type == "human")


def _status(carrinho: dict, cliente: dict) -> str:
    """Resumo devolvido ao LLM após cada tool, para ele saber o que ainda falta."""
    itens = ", ".join(f"{qtd}x {CATALOGO[pid]['nome']}" for pid, qtd in carrinho.items()) or "vazio"
    faltam = [rotulo for campo, rotulo in CAMPOS_CLIENTE.items() if campo not in cliente]
    return (f"Carrinho: {itens}. Total: {brl(total(carrinho))}. "
            f"Dados do cliente faltando: {', '.join(faltam) or 'nenhum'}.")


def _resposta(tool_call_id: str, texto: str, **atualizacoes) -> Command:
    return Command(update={"messages": [ToolMessage(texto, tool_call_id=tool_call_id)], **atualizacoes})


@tool
def listar_produtos(categoria: str = "") -> str:
    """Consulta o catálogo. Informe uma categoria para filtrar ou deixe vazio para listar tudo."""
    itens = [
        {"id": pid, "nome": p["nome"], "categoria": p["categoria"],
         "preco": brl(p["preco"]), "destaques": p["destaques"]}
        for pid, p in CATALOGO.items()
        if categoria.lower() in p["categoria"].lower()
    ]
    if not itens:
        return f"Nenhum produto em '{categoria}'. Categorias disponíveis: {', '.join(CATEGORIAS)}."
    return json.dumps(itens, ensure_ascii=False)


@tool
def escolher_produto(
    produto: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    quantidade: int = 1,
) -> Command:
    """Coloca um produto no carrinho com a quantidade desejada (0 remove). Use quando o cliente decidir comprar.
    Em `produto`, informe o nome do produto ou o id devolvido por listar_produtos."""
    # Aceitar o nome evita que modelos pequenos inventem um id quando o cliente pede o produto direto.
    produto_id = achar_produto(produto)
    if not produto_id:
        return _resposta(tool_call_id, f"Produto '{produto}' não encontrado. Consulte listar_produtos.")
    if not 0 <= quantidade <= 10:
        return _resposta(tool_call_id, "A quantidade precisa ficar entre 0 e 10.")
    carrinho = _mesclar(state["carrinho"], {produto_id: quantidade})
    # A instrução vai no retorno, e não só no prompt: o modelo local seguia mais o que leu por último.
    texto = "Carrinho atualizado. Confirme ao cliente os itens e o total antes de pedir os dados. "
    return _resposta(tool_call_id, texto + _status(carrinho, state["cliente"]), carrinho={produto_id: quantidade})


@tool
def registrar_cliente(
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    nome: str = "",
    email: str = "",
    telefone: str = "",
) -> Command:
    """Registra os dados que o cliente informou. Envie só os campos que ele escreveu na conversa; os demais ficam como
    estão. Nunca complete um dado que ele não passou."""
    dados, erros = {}, []
    # O modelo local chegou a registrar um telefone "1234567890" quando o cliente só passou o e-mail.
    nao_informado = "o cliente não informou o {} (não invente, pergunte a ele)"
    if nome.strip():
        # O nome volta ao cliente no texto da proposta, que não passa pela moderação do LLM.
        if contem_palavrao(nome):
            erros.append("nome inválido")
        elif not cliente_digitou(nome, state["messages"]):
            erros.append(nao_informado.format("nome"))
        else:
            dados["nome"] = nome.strip()
    if email:
        if not EMAIL_RE.fullmatch(email.strip()):
            erros.append(f"e-mail '{email}' inválido")
        elif not cliente_digitou(email.strip(), state["messages"]):
            erros.append(nao_informado.format("e-mail"))
        else:
            dados["email"] = email.strip().lower()
    if telefone:
        if not (fone := formatar_telefone(telefone)):
            erros.append(f"telefone '{telefone}' inválido (precisa ter DDD e número)")
        elif not cliente_digitou(fone, state["messages"], so_digitos=True):
            erros.append(nao_informado.format("telefone"))
        else:
            dados["telefone"] = fone
    cliente = _mesclar(state["cliente"], dados)
    texto = (f"Erro: {'; '.join(erros)}. " if erros else "Dados registrados. ") + _status(state["carrinho"], cliente)
    return _resposta(tool_call_id, texto, cliente=dados)


def gerar_proposta(estado: Estado) -> dict:
    """Proposta montada em Python: preços e link nunca vêm do LLM."""
    carrinho, cliente = estado["carrinho"], estado["cliente"]
    linhas = "\n".join(
        f"| {CATALOGO[pid]['nome']} | {qtd} | {brl(CATALOGO[pid]['preco'])} | {brl(CATALOGO[pid]['preco'] * qtd)} |"
        for pid, qtd in carrinho.items()
    )
    valor = total(carrinho)
    link = f"https://checkout.example.com/{uuid.uuid4().hex[:10]}"
    texto = (
        f"Excelente, {cliente['nome']}! Aqui está sua proposta:\n\n"
        f"| Produto | Qtd. | Preço | Subtotal |\n|---|---:|---:|---:|\n{linhas}\n\n"
        f"**Total: {brl(valor)}**, ou 12x de {brl(valor / 12)} sem juros.\n\n"
        f"Contato: {cliente['email']} · {cliente['telefone']}\n\n"
        f"Para finalizar, é só concluir o checkout em: {link}"
    )
    return {"messages": [AIMessage(texto)],
            "proposta": {"carrinho": carrinho, "cliente": cliente, "total": valor, "link": link}}


def transferir_para_humano(estado: Estado) -> dict:
    texto = ("Pedidos desse valor são finalizados com um de nossos especialistas, para te dar uma atenção especial. "
             "Já transferi seu atendimento, e um especialista continua a conversa por aqui em instantes. Obrigado!")
    return {"messages": [AIMessage(texto)], "handoff": True}


def rota(estado: Estado) -> str:
    return {"handoff": "handoff", "gerar_proposta": "proposta"}.get(etapa(estado), "agente")


def criar_llm(temperatura: float | None = None):
    """Modelo de MODELO. `temperatura` vale só para o Ollama (o classificador de moderação usa 0)."""
    if MODELO.startswith("claude"):
        return ChatAnthropic(model=MODELO, effort="low", max_tokens=16_000)
    # Sem "thinking" as respostas saem bem mais rápidas; o contexto padrão do Ollama (4k) não cabe o prompt e o histórico.
    # O num_ctx é o mesmo no bot e no classificador: com outro valor, o Ollama recarregaria o modelo a cada troca.
    return ChatOllama(model=MODELO, reasoning=False, num_ctx=8_192, temperature=temperatura)


def construir_grafo(llm=None, banco=BANCO, moderar=None):
    """Monta o grafo. `llm`, `banco` (":memory:") e `moderar` permitem testar com modelo falso, sem gravar arquivo
    e sem chamar o classificador de moderação."""
    tools = [listar_produtos, escolher_produto, registrar_cliente]
    llm = (llm or criar_llm()).bind_tools(tools)
    moderar = moderar or criar_moderador(criar_llm(temperatura=0))
    sistema = SystemMessage(PROMPT.format(categorias=", ".join(CATEGORIAS)))

    def agente(estado: Estado) -> dict:
        resposta = llm.invoke([sistema, *estado["messages"]])
        if resposta.response_metadata.get("stop_reason") == "refusal":
            resposta = AIMessage("Desculpe, não posso ajudar com isso. Posso te ajudar a escolher um eletrônico?")
        elif resposta.text and (etapa := moderar(resposta.text)):
            # Guardrail de saída: o texto barrado não chega ao cliente; o original fica guardado para auditoria.
            resposta = AIMessage(
                "" if resposta.tool_calls else "Desculpe, não consegui responder agora. Pode reformular sua mensagem?",
                tool_calls=resposta.tool_calls,
                additional_kwargs={"moderacao": {"etapa": etapa, "original": resposta.text}},
            )
        return {"messages": [resposta]}

    grafo = StateGraph(Estado)
    grafo.add_node("agente", agente)
    grafo.add_node("tools", ToolNode(tools))
    grafo.add_node("proposta", gerar_proposta)
    grafo.add_node("handoff", transferir_para_humano)
    grafo.add_edge(START, "agente")
    grafo.add_conditional_edges("agente", tools_condition)
    grafo.add_conditional_edges("tools", rota, ["agente", "proposta", "handoff"])
    grafo.add_edge("proposta", END)
    grafo.add_edge("handoff", END)
    # check_same_thread=False: o Streamlit atende cada mensagem numa thread; o SqliteSaver serializa o acesso.
    return grafo.compile(checkpointer=SqliteSaver(sqlite3.connect(banco, check_same_thread=False)))
