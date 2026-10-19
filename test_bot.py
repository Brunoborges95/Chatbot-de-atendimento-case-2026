"""Testes das regras do bot, sem LLM: rodam em segundos e dão sempre o mesmo resultado. Uso: pytest"""

from langchain_core.messages import HumanMessage

from bot import etapa, formatar_telefone, gerar_proposta, registrar_cliente

MARIA = {"nome": "Maria Souza", "email": "maria@email.com", "telefone": "(11) 98765-4321"}


def registrar(mensagens_do_cliente: list[str], **dados) -> dict:
    """Chama a tool como o LLM chamaria, depois de o cliente mandar essas mensagens. Devolve o que ela registrou."""
    estado = {"messages": [HumanMessage(m) for m in mensagens_do_cliente], "carrinho": {}, "cliente": {}}
    return registrar_cliente.func(state=estado, tool_call_id="teste", **dados).update["cliente"]


def test_formata_telefone():
    assert formatar_telefone("+55 (11) 98765-4321") == "(11) 98765-4321"
    assert formatar_telefone("1133334444") == "(11) 3333-4444"
    assert formatar_telefone("98765-4321") is None  # sem DDD


def test_registra_dados_que_o_cliente_digitou():
    assert registrar(["maria@email.com e 11 98765-4321"], email="maria@email.com", telefone="11987654321") == {
        "email": "maria@email.com", "telefone": "(11) 98765-4321"}


def test_recusa_email_invalido():
    assert registrar(["maria.email.com"], email="maria.email.com") == {}


def test_recusa_dado_que_o_cliente_nao_digitou():
    # Caso real: o cliente passou só o e-mail e o modelo inventou o telefone.
    assert registrar(["maria@email.com"], email="maria@email.com", telefone="1234567890") == {"email": "maria@email.com"}


def test_etapas_da_venda():
    assert etapa({}) == "escolher_produto"
    assert etapa({"carrinho": {"novophone-x12": 1}}) == "coletar_dados"
    assert etapa({"carrinho": {"novophone-x12": 1}, "cliente": MARIA}) == "gerar_proposta"


def test_transfere_acima_do_cap():
    assert etapa({"carrinho": {"ultrabook-pro-15": 1}}) == "handoff"  # R$ 7.999,00
    assert etapa({"carrinho": {"novophone-x12": 1, "timewatch-s2": 2}}) == "handoff"  # vale o total: R$ 6.097,00


def test_proposta():
    estado = {"carrinho": {"novophone-x12": 1, "soundbuds-plus": 2}, "cliente": MARIA}
    saida = gerar_proposta(estado)
    texto, proposta = saida["messages"][0].text, saida["proposta"]
    assert "R$ 4.297,00" in texto and "Maria Souza" in texto and proposta["link"] in texto
    assert etapa({**estado, "proposta": proposta}) == "concluido"
