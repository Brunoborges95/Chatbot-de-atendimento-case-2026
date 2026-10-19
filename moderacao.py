"""Guardrail de moderação em duas etapas: lista de palavrões (determinística) e classificação pelo próprio LLM."""

import re
import unicodedata
from collections.abc import Callable

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

# Padrões já normalizados (minúsculas, sem acento). Prefixos com cuidado: "viad[oa]" e não "viad", por causa de "viaduto".
PALAVROES = [
    r"porra\w*", r"caralh\w*", r"merd\w*", r"fod\w*", r"fud\w*", r"puta\w*", r"puto\w*", r"putari\w*",
    r"buceta\w*", r"boceta\w*", r"cu", r"cuzao\w*", r"arrombad\w*", r"cacete", r"bosta\w*", r"babaca\w*",
    r"otari[oa]s?", r"idiota\w*", r"imbecil", r"imbecis", r"retardad[oa]s?", r"desgracad[oa]s?",
    r"vagabund[oa]s?", r"piranha\w*", r"vadia\w*", r"corno\w*", r"viad[oa]s?", r"viadinh\w*", r"bichas?",
    r"fdp", r"pqp", r"vsf", r"tnc", r"vtnc", r"krl", r"crl",
]
_PALAVRAO_RE = re.compile(r"\b(?:" + "|".join(PALAVROES) + r")\b")
_LEET = str.maketrans("0134@$", "oieaas")  # grafias disfarçadas: "p0rra", "@rrombado"

PROMPT_MODERACAO = """\
Você revisa as respostas de um assistente de vendas de uma loja de eletrônicos antes de elas chegarem ao cliente.

Marque a resposta como inadequada se ela tiver:
- palavrão, xingamento ou insulto, mesmo disfarçado ou abreviado;
- deboche, ironia ou desrespeito com o cliente;
- conteúdo sexual ou obsceno;
- discurso de ódio, preconceito ou ameaça.

É adequado recusar um pedido, responder com educação a um cliente grosseiro, pedir dados para a compra ou falar de \
produtos, preços e pagamento. Avalie só o texto da resposta."""


class Avaliacao(BaseModel):
    """Resultado da revisão de uma resposta do assistente."""

    inadequada: bool = Field(description="true se a resposta não pode ser enviada ao cliente")


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acento.lower().translate(_LEET)


def contem_palavrao(texto: str) -> bool:
    """Etapa 1: lista de palavrões. Rápida e determinística, pega o óbvio."""
    return bool(_PALAVRAO_RE.search(_normalizar(texto)))


def criar_moderador(llm) -> Callable[[str], str | None]:
    """Devolve a função de moderação: a etapa que barrou o texto ("lista" ou "classificador"), ou None se ele pode ir
    ao cliente. A etapa 2 usa o próprio LLM como classificador e pega ofensa sem palavrão (deboche, desrespeito)."""
    classificador = llm.with_structured_output(Avaliacao)

    def moderar(texto: str) -> str | None:
        if contem_palavrao(texto):
            return "lista"
        avaliacao = classificador.invoke([SystemMessage(PROMPT_MODERACAO), HumanMessage(f"<resposta>\n{texto}\n</resposta>")])
        return "classificador" if avaliacao.inadequada else None

    return moderar
