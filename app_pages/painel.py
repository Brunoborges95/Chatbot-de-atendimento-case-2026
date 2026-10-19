"""Página de métricas: calculadas a partir das conversas salvas, sem LLM."""

import streamlit as st

from bot import ETAPAS
from metricas import carregar_conversas, contar_problemas, funil, indicadores, tabela

AJUDA = {
    "Taxa de proposta": "Conversas que terminaram com proposta e link de checkout.",
    "Transferidas para humano": "Pedidos acima do CAP, encaminhados a um especialista.",
    "Mensagens até a proposta": "Mediana de mensagens do cliente até receber a proposta.",
    "Conversas com problema": "Conversas que violaram alguma regra (veja abaixo).",
    "Respostas barradas": "Respostas do bot que a moderação impediu de chegar ao cliente.",
}

st.set_page_config(layout="wide")
st.title("Métricas")
st.caption("Calculadas a partir do histórico de conversas salvo no banco. Atualizam sozinhas a cada 5 segundos.")


@st.fragment(run_every="5s")
def painel():
    df = tabela(carregar_conversas())
    if df.empty:
        st.info("Ainda não há conversas. Converse com o bot na página Chat.", icon=":material/forum:")
        return

    with st.container(horizontal=True):
        for rotulo, valor in indicadores(df).items():
            st.metric(rotulo, valor, border=True, help=AJUDA.get(rotulo))

    esquerda, direita = st.columns(2)
    with esquerda.container(border=True):
        st.subheader("Funil de vendas")
        st.bar_chart(funil(df), x="etapa", y="conversas", horizontal=True, sort=False,
                     x_label="Conversas", y_label="", alt="Funil: quantas conversas chegaram a cada etapa")
    with direita.container(border=True):
        st.subheader("Problemas encontrados")
        contagem = contar_problemas(df)
        if contagem.empty:
            st.success("Nenhuma regra violada.", icon=":material/check_circle:")
        else:
            st.dataframe(contagem, hide_index=True, alt="Problemas encontrados por tipo")
        st.caption("Total da proposta igual ao catálogo, proposta nunca acima do CAP, transferência acima do CAP, "
                   "nenhum link inventado, e-mail ou telefone digitados pelo cliente registrados e nenhum dado do "
                   "cliente inventado.")

    with st.container(border=True):
        st.subheader("Conversas")
        st.dataframe(
            df.assign(etapa=df.etapa.map(ETAPAS), problemas=df.problemas.str.join("; ")),
            hide_index=True,
            column_order=["abrir", "atualizada", "etapa", "itens", "total", "mensagens_cliente", "erros_tool",
                          "respostas_barradas", "problemas"],
            column_config={
                "abrir": st.column_config.LinkColumn("Abrir", display_text=":material/open_in_new:", width="small"),
                "atualizada": st.column_config.DatetimeColumn("Atualizada", format="DD/MM HH:mm"),
                "etapa": "Etapa",
                "itens": "Itens",
                "total": "Total",
                "mensagens_cliente": st.column_config.NumberColumn("Mensagens", help="Mensagens do cliente"),
                "erros_tool": st.column_config.NumberColumn(
                    "Erros de tool", help="Dado inválido, produto inexistente ou quantidade fora do limite"),
                "respostas_barradas": st.column_config.NumberColumn("Barradas", help="Respostas barradas pela moderação"),
                "problemas": "Problemas",
            },
            alt="Conversas salvas, da mais recente para a mais antiga",
        )


painel()
