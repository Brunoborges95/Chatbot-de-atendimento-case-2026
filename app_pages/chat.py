"""Página de chat com o assistente de vendas."""

import uuid

import streamlit as st
from langchain_core.messages import HumanMessage

from bot import CAMPOS_CLIENTE, CAP, CATALOGO, ETAPAS, brl, construir_grafo, etapa, total


@st.cache_resource
def carregar_grafo():
    return construir_grafo()


def md(texto: str) -> str:
    """Escapa o cifrão para "R$" não virar fórmula LaTeX no Markdown do Streamlit."""
    return texto.replace("$", r"\$")


# O id da conversa fica no endereço (recarregar ou reabrir o link retoma a conversa salva)
# e na sessão (voltar da página de métricas mantém a conversa aberta).
conversa = st.query_params.get("conversa") or st.session_state.get("conversa") or str(uuid.uuid4())
st.query_params["conversa"] = st.session_state.conversa = conversa
config = {"configurable": {"thread_id": conversa}}
grafo = carregar_grafo()
estado = grafo.get_state(config).values

with st.sidebar:
    st.header("Pedido")
    st.caption(f"Etapa: {ETAPAS[etapa(estado)]}")
    carrinho = estado.get("carrinho", {})
    for pid, qtd in carrinho.items():
        st.markdown(md(f"{qtd}x {CATALOGO[pid]['nome']}: {brl(CATALOGO[pid]['preco'] * qtd)}"))
    st.metric("Total", brl(total(carrinho)))
    st.caption(md(f"Acima de {brl(CAP)}, o atendimento vai para um especialista."))

    st.header("Cliente")
    cliente = estado.get("cliente", {})
    for campo, rotulo in CAMPOS_CLIENTE.items():
        st.markdown(f"**{rotulo.capitalize()}:** {cliente.get(campo, '—')}")

    if st.button("Nova conversa", icon=":material/refresh:", width="stretch"):
        # A conversa anterior continua salva no banco.
        st.query_params["conversa"] = st.session_state.conversa = str(uuid.uuid4())
        st.rerun()

st.title("Marketplace")
st.caption("Assistente de vendas de eletrônicos. Conte o que você procura para começar.")

for msg in estado.get("messages", []):
    if msg.type in ("human", "ai") and msg.text:
        with st.chat_message("user" if msg.type == "human" else "assistant"):
            st.markdown(md(msg.text))

encerrado = estado.get("handoff", False)
if encerrado:
    st.info("Atendimento transferido para um especialista.", icon=":material/support_agent:")

if pergunta := st.chat_input("Escreva sua mensagem", disabled=encerrado):
    with st.chat_message("user"):
        st.markdown(md(pergunta))
    with st.chat_message("assistant"), st.spinner("Digitando..."):
        grafo.invoke({"messages": [HumanMessage(pergunta)]}, config)
    st.rerun()
