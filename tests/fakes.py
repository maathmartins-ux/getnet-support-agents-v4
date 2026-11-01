"""Dublês sem rede: cliente Anthropic roteirizado, blocos de resposta e embedder determinístico."""
from types import SimpleNamespace as NS

from app.llm import AnthropicLLM


class FakeMessages:
    """Roteiro de respostas da API: cada create() devolve o próximo item e guarda os kwargs enviados.

    Item pode ser uma resposta, uma exceção (é lançada) ou uma função kwargs -> resposta.
    """

    def __init__(self, script):
        self.script, self.sent = list(script), []

    def create(self, **kwargs):
        # cópia rasa da lista: o loop continua anexando na mesma lista depois desta chamada
        self.sent.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item(kwargs) if callable(item) else item


def resp(stop, *blocks, i=100, o=20):
    return NS(stop_reason=stop, content=list(blocks), usage=NS(input_tokens=i, output_tokens=o))


def text(t):
    return NS(type="text", text=t)


def tool_use(name, inp, bid="tu1"):
    return NS(type="tool_use", name=name, input=inp, id=bid)


def fake_llm(script, model="claude-sonnet-5-5", effort="low"):
    msgs = FakeMessages(script)
    return AnthropicLLM(model, effort, client=NS(messages=msgs)), msgs


class ConceptEmbedder:
    """Embedder falso: um eixo por conceito, ativado por radicais. Pega sinônimo que o BM25 não vê."""

    name = "fake-concepts"
    CONCEPTS = [("entreg", "prazo", "chega", "dias úteis"), ("troc", "devol", "reembols", "estorn"),
                ("horári", "atendiment", "funciona", "e-mail", "suporte")]

    def embed(self, texts):
        return [[float(sum(t.lower().count(stem) for stem in group)) + 1e-6 for group in self.CONCEPTS]
                for t in texts]
