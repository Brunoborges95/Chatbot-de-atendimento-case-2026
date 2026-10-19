"""Interface (Streamlit) do assistente de vendas: chat e métricas."""

import streamlit as st

st.set_page_config(page_title="Marketplace", page_icon=":material/storefront:")

pagina = st.navigation(
    [
        st.Page("app_pages/chat.py", title="Chat", icon=":material/chat:", default=True),
        st.Page("app_pages/painel.py", title="Métricas", icon=":material/monitoring:", url_path="metricas"),
    ],
    position="top",
)
pagina.run()
