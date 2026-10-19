"""Métricas do atendimento, calculadas só a partir do histórico de conversas gravado no banco (sem LLM)."""

import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from langgraph.checkpoint.sqlite import SqliteSaver

from bot import BANCO, CAMPOS_CLIENTE, CAP, CATALOGO, EMAIL_RE, brl, cliente_digitou, etapa, formatar_telefone, total

FUNIL = ["Conversa iniciada", "Produto escolhido", "Dados completos", "Proposta enviada"]
LINK_RE = re.compile(r"https?://\S+")
TELEFONE_RE = re.compile(r"(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}")
# Começo dos retornos de erro das tools em bot.py (dado inválido, produto inexistente, quantidade fora do limite).
ERROS_TOOL = ("Erro:", "Produto '", "A quantidade")


def carregar_conversas(banco: Path = BANCO) -> list[dict]:
    """Estado mais recente de cada conversa, da mais recente para a mais antiga."""
    if not Path(banco).exists():
        return []
    with SqliteSaver.from_conn_string(str(banco)) as saver:
        saver.setup()
        ids = [t for (t,) in saver.conn.execute("SELECT DISTINCT thread_id FROM checkpoints")]
        tuplas = [saver.get_tuple({"configurable": {"thread_id": t}}) for t in ids]
    conversas = [{"id": t, "atualizada": datetime.fromisoformat(tp.checkpoint["ts"]).astimezone(),
                  "estado": tp.checkpoint["channel_values"]} for t, tp in zip(ids, tuplas)]
    return sorted(conversas, key=lambda c: c["atualizada"], reverse=True)


def problemas(estado: dict) -> list[str]:
    """Regras que valem para qualquer conversa. Lista vazia = nenhuma violada."""
    erros = []
    proposta = estado.get("proposta") or {}
    if proposta and proposta["total"] != total(proposta["carrinho"]):
        erros.append("Total da proposta diferente do catálogo")
    if proposta and proposta["total"] > CAP:
        erros.append("Proposta acima do CAP")
    if total(estado.get("carrinho", {})) > CAP and not estado.get("handoff"):
        erros.append("Carrinho acima do CAP sem transferência")
    mensagens = estado.get("messages", [])
    links = {link for m in mensagens if m.type == "ai" for link in LINK_RE.findall(m.text)}
    if links - {proposta.get("link")}:
        erros.append("Link inventado pelo modelo")

    # O próprio histórico dá o esperado: o último e-mail e telefone válidos que o cliente digitou têm de estar registrados,
    # e nada registrado pode faltar nas mensagens dele.
    cliente = estado.get("cliente", {})
    texto = " ".join(m.text for m in mensagens if m.type == "human")
    emails = [e.rstrip(".,;!?").lower() for e in EMAIL_RE.findall(texto)]
    if emails and emails[-1] != cliente.get("email"):
        erros.append("E-mail informado e não registrado")
    telefones = [f for t in TELEFONE_RE.findall(texto) if (f := formatar_telefone(t))]
    if telefones and telefones[-1] != cliente.get("telefone"):
        erros.append("Telefone informado e não registrado")
    if any(not cliente_digitou(valor, mensagens, so_digitos=campo == "telefone") for campo, valor in cliente.items()):
        erros.append("Dado do cliente inventado pelo modelo")
    return erros


def resumir(conversa: dict) -> dict:
    """Uma linha por conversa, com o que as métricas precisam."""
    estado = conversa["estado"]
    mensagens = estado.get("messages", [])
    carrinho = estado.get("carrinho", {})
    proposta = bool(estado.get("proposta"))
    handoff = bool(estado.get("handoff"))
    # Mensagens do cliente até a primeira proposta (a única mensagem com link é a proposta).
    i_proposta = next((i for i, m in enumerate(mensagens) if m.type == "ai" and LINK_RE.search(m.text)), None)
    return {
        "id": conversa["id"],
        "abrir": f"/?conversa={conversa['id']}",
        "atualizada": conversa["atualizada"],
        "etapa": etapa(estado),
        "itens": ", ".join(f"{qtd}x {CATALOGO[pid]['nome']}" for pid, qtd in carrinho.items()),
        "total": brl(total(carrinho)) if carrinho else "",
        "mensagens_cliente": sum(m.type == "human" for m in mensagens),
        "turnos_ate_proposta": sum(m.type == "human" for m in mensagens[:i_proposta]) if proposta else None,
        "erros_tool": sum(m.type == "tool" and m.text.startswith(ERROS_TOOL) for m in mensagens),
        "respostas_barradas": sum(m.type == "ai" and "moderacao" in m.additional_kwargs for m in mensagens),
        "produto": bool(carrinho) or proposta or handoff,
        "dados": not CAMPOS_CLIENTE.keys() - estado.get("cliente", {}).keys(),
        "proposta": proposta,
        "handoff": handoff,
        "problemas": problemas(estado),
    }


def tabela(conversas: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([resumir(c) for c in conversas])


def indicadores(df: pd.DataFrame) -> dict:
    """Números do topo do painel."""
    turnos = df.turnos_ate_proposta.dropna()
    return {
        "Conversas": len(df),
        "Taxa de proposta": f"{df.proposta.mean():.0%}",
        "Transferidas para humano": f"{df.handoff.mean():.0%}",
        "Mensagens até a proposta": f"{turnos.median():.0f}" if len(turnos) else "—",
        "Conversas com problema": int((df.problemas.str.len() > 0).sum()),
        "Respostas barradas": int(df.respostas_barradas.sum()),
    }


def contar_problemas(df: pd.DataFrame) -> pd.DataFrame:
    """Quantas conversas violaram cada regra."""
    return df.problemas.explode().dropna().value_counts().rename_axis("problema").reset_index(name="conversas")


def moderadas(conversas: list[dict]) -> pd.DataFrame:
    """Respostas barradas pela moderação, com o texto original, para revisar falsos positivos."""
    return pd.DataFrame([
        {"conversa": c["id"], "etapa": m.additional_kwargs["moderacao"]["etapa"],
         "original": m.additional_kwargs["moderacao"]["original"]}
        for c in conversas for m in c["estado"].get("messages", [])
        if m.type == "ai" and "moderacao" in m.additional_kwargs
    ], columns=["conversa", "etapa", "original"])


def funil(df: pd.DataFrame) -> pd.DataFrame:
    """Quantas conversas chegaram a cada etapa da venda."""
    contagens = [len(df), df.produto.sum(), (df.produto & df.dados).sum(), df.proposta.sum()]
    return pd.DataFrame({"etapa": FUNIL, "conversas": contagens})
