"""Router: intenção + plano inicial + idioma + confiança.

Com LLM: papel "router" (Haiku 5.5, effort low) com saída em JSON schema (output_config.format).
Offline (CI) e em qualquer falha do LLM (erro, recusa, JSON inválido): regras determinísticas abaixo.
O plano é só o ponto de partida — quem decide os passos seguintes é o supervisor.

As regras e a taxonomia são do DOMÍNIO (app/domain/<ativo>.py: clause_intent, combine, states_subject, PLANS);
este arquivo é motor e não muda no desafio. A interface `route(text, history) -> RouterDecision` fica.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path

from . import domain
from .config import data_dir
from .llm import LLMError, get_llm
from .observability import METRICS
from .rag import get_index, tokenize
from .schemas import RouterDecision


def _plans() -> dict[str, list[str]]:
    return domain.active().PLANS


def schema() -> dict:
    """JSON schema da saída do router: o enum de intents vem do domínio ativo."""
    return {
        "type": "object", "additionalProperties": False,
        "required": ["intent", "plan", "language", "confidence"],
        "properties": {
            "intent": {"type": "string", "enum": sorted(_plans())},
            "plan": {"type": "array", "items": {"type": "string", "enum": ["faq", "records", "escalation"]}},
            "language": {"type": "string", "enum": ["pt", "en"]},
            "confidence": {"type": "number"},
        },
    }

# Confirmação no começo da fala ("Ok, vou tentar...", "Beleza, obrigado!"): não muda o assunto da conversa.
_ACK = re.compile(r"^\W*(ok(ay)?|t[aá] bom|certo|beleza|blz|valeu|obrigad[oa]|entendi|show|perfeito|combinado"
                  r"|thanks|thank you|got it|sure|alright|fine|cool)\b", re.IGNORECASE)
# Palavras funcionais frequentes de cada idioma; nenhuma de uma letra só ("a" existe nos dois).
_EN = re.compile(r"\b(the|what|how|why|when|my|is|are|can|you|please|want|to|talk|with|it|do|does"
                 r"|of|and|for|this|that|me|need|help|where|order)\b", re.IGNORECASE)
_PT = re.compile(r"\b(os|as|um|uma|de|do|da|que|como|com|para|por|não|meu|minha|quero|está"
                 r"|falar|ajuda|preciso|sem|no|na|qual|onde|posso|pedido)\b", re.IGNORECASE)
_ACCENT = re.compile(r"[ãõáéíóúâêôç]", re.IGNORECASE)
# Fronteira de cláusula: pontuação, ":" (não em "9:30"), conjunção que emenda outra pergunta (", and",
# " e aproveita", " e também", "also", "além disso"). " e "/" and " soltos NÃO dividem ("troca e devolução").
# v3: "... e aproveita: ...?" e "two questions: ..., and ...?" viravam uma intenção só.
_CLAUSE = re.compile(r"[?.!;]+|:(?!\d)|,\s*(?:and|e)\s+|,?\s+e\s+(?:aproveita|tamb[eé]m)\b"
                     r"|,?\s*\balso\b|,?\s*\bal[eé]m disso\b", re.IGNORECASE)
# Quem anuncia "duas perguntas" avisou que há duas: aí " e "/" and " e vírgula também dividem.
_TWO_QUESTIONS = re.compile(r"\b(?:duas|2)\s+(?:perguntas|d[uú]vidas|coisas)\b|\b(?:two|2)\s+(?:questions|things)\b",
                            re.IGNORECASE)
_AND = re.compile(r",|\s+(?:e|and)\s+", re.IGNORECASE)
# Conversa que não é assunto: sai do vocabulário antes da checagem ("Bom dia" casaria com "dias úteis").
_SMALLTALK = set(tokenize("oi olá ola bom boa dia tarde noite tudo bem hoje obrigado obrigada valeu enfim nossa "
                          "hello hi hey good morning afternoon evening thanks thank please"))


def split_clauses(text: str) -> list[str]:
    parts = [c for c in _CLAUSE.split(text) if c and c.strip()]
    if _TWO_QUESTIONS.search(text):
        parts = [p for c in parts for p in _AND.split(c) if p.strip()]
    return parts or [text]


def _stamp(d: Path) -> tuple:
    # o domínio ativo entra na chave dos caches: exemplos com intent fora da taxonomia dele são descartados
    return (domain.name(),) + tuple((p.name, p.stat().st_mtime_ns) for p in sorted(d.rglob("*.jsonl")))


EXAMPLES_FILE = "intent_examples.jsonl"  # {"text", "intent"}; o domínio preenche no relógio. NUNCA o held-out.


@lru_cache(maxsize=4)
def _examples_for(directory: str, stamp: tuple) -> tuple[tuple[str, str], ...]:
    from app.jsonl import read_family

    rows = read_family(Path(directory) / EXAMPLES_FILE)  # base + intent_examples.d/*.jsonl
    return tuple((r["text"], r["intent"]) for r in rows if r.get("text") and r.get("intent") in _plans())


@lru_cache(maxsize=4)
def _vocab_for(directory: str, stamp: tuple) -> frozenset[str]:
    """Vocabulário do domínio = base de conhecimento (título + texto) + exemplos rotulados, sem conversa."""
    texts = [f"{d.title} {d.text}" for d in get_index().docs] + [t for t, _ in _examples_for(directory, stamp)]
    return frozenset(w for t in texts for w in tokenize(t)) - _SMALLTALK


def has_subject(text: str) -> bool:
    """A cláusula fala de algo do domínio? "Hoje tá frio, né?" não; "qual o prazo?" sim."""
    d = data_dir()
    return bool((set(tokenize(text)) - _SMALLTALK) & _vocab_for(str(d), _stamp(d)))


# --- k-NN offline: recurso de quando nenhuma regra casa -------------------------------------------------
# TF-IDF de n-gramas de caractere (3 a 5) sobre texto normalizado, cosseno, k vizinhos com voto ponderado pela
# similaridade. Caractere (e não palavra) pega flexão e erro de digitação: "maquininha"/"maquinha", "ontm".
KNN_K = 5
KNN_NGRAMS = (3, 5)
# Limiar mínimo de similaridade do vizinho MAIS próximo; abaixo dele, o k-NN se cala e vale o padrão de sempre.
# 0,15 medido em 31/10/2026, nunca no held-out:
# - leave-one-out nos 78 casos de desenvolvimento da v3 (evals/cases.jsonl, sem multi/blocked): acerto 88,5%,
#   precisão 91,4% acima do limiar, cobertura 90%. Em 0,10: 91,0% de acerto, mas 4 de 12 frases de conversa
#   passavam; em 0,25: 78,2% de acerto.
# - conversa genuína ("Ok, entendi.", "What a rainy day, right?", "Tudo bem com você?") ficou ≤ 0,117 nos
#   dois conjuntos; a paráfrase certa mais fraca do fixture do kit, 0,195.
# Exemplo de treino deve ser UMA cláusula de UMA intenção: "Oi! Hoje tá frio, né? Enfim, quanto vendi..." como
# exemplo puxou "Hoje tá frio, né?" para 0,54. Remeça quando o domínio trocar os exemplos.
KNN_MIN_SIM = 0.15
FEW_SHOT_K = 8  # exemplos mais parecidos no prompt do router com LLM


def normalize(text: str) -> str:
    """Minúsculas, sem acento, só letras e dígitos separados por espaço."""
    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", plain))


def _ngrams(text: str) -> Counter:
    t = f" {normalize(text)} "
    lo, hi = KNN_NGRAMS
    return Counter(t[i:i + n] for n in range(lo, hi + 1) for i in range(len(t) - n + 1))


class KNN:
    """k-NN determinístico sobre exemplos rotulados; só biblioteca padrão."""

    def __init__(self, examples: list[tuple[str, str]] | tuple[tuple[str, str], ...]):
        self.examples = list(examples)
        grams = [_ngrams(t) for t, _ in self.examples]
        df = Counter(g for c in grams for g in c)
        n = len(self.examples)
        self.idf = {g: math.log((1 + n) / (1 + d)) + 1 for g, d in df.items()}  # idf suavizado
        self.idf_unseen = math.log(1 + n) + 1  # n-grama nunca visto pesa como o mais raro: frase alheia fica longe
        self.vectors = [self._vector(c) for c in grams]

    def _vector(self, counts: Counter) -> dict[str, float]:
        v = {g: (1 + math.log(c)) * self.idf.get(g, self.idf_unseen) for g, c in counts.items()}
        norm = math.sqrt(sum(x * x for x in v.values()))
        return {g: x / norm for g, x in v.items()} if norm else {}

    def neighbors(self, text: str, k: int) -> list[tuple[float, int]]:
        """(similaridade, índice do exemplo), do mais parecido ao menos; empate desfeito pela ordem do arquivo."""
        q = self._vector(_ngrams(text))
        sims = [(sum(w * vec.get(g, 0.0) for g, w in q.items()), i) for i, vec in enumerate(self.vectors)]
        return sorted(sims, key=lambda s: (-s[0], s[1]))[:k]

    def predict(self, text: str, k: int = KNN_K, min_sim: float = KNN_MIN_SIM) -> str | None:
        top = self.neighbors(text, k)
        if not top or top[0][0] < min_sim:
            return None
        votes: Counter = Counter()
        for sim, i in top:
            votes[self.examples[i][1]] += sim
        return min(votes, key=lambda intent: (-votes[intent], intent))  # maior soma; empate: ordem alfabética


@lru_cache(maxsize=8)
def _knn_for(directory: str, stamp: tuple, clause_level: bool) -> KNN:
    examples = _examples_for(directory, stamp)
    # O classificador roda por CLÁUSULA: multi nasce da soma de cláusulas, não de um vizinho. Medido no fixture:
    # com exemplos multi no voto, "cadê minha encomenda" virava multi (0,76 contra 0,62 de records).
    return KNN([e for e in examples if e[1] != "multi"] if clause_level else examples)


def get_knn(clause_level: bool = True) -> KNN:
    """clause_level=True: classificador offline (sem exemplos multi). False: few-shot do LLM (todos)."""
    d = data_dir()
    return _knn_for(str(d), _stamp(d), clause_level)


def knn_intent(text: str) -> str | None:
    return get_knn().predict(text)


def detect_language(text: str) -> str:
    # Acento vale 1 ponto para pt, não decide sozinho (nome próprio acentuado em frase inglesa).
    en = len(_EN.findall(text))
    pt = len(_PT.findall(text)) + min(1, len(_ACCENT.findall(text)))
    return "en" if en > pt else "pt"


def _states_subject(text: str) -> bool:
    """Fala que define assunto: casa uma regra ou tem vocabulário do domínio, e não é só confirmação."""
    if domain.active().states_subject(text):
        return True
    return not _ACK.match(text) and has_subject(text)


def topic_intent(history: list[dict]) -> str | None:
    """Intenção da última fala do cliente COM ASSUNTO, pulando confirmação e conversa ("Ok, vou tentar...").

    Olhar só a última fala quebrava a cadeia: memory-04 da v3 perdia o problema depois de um "Ok".
    Pergunta de seguimento ("e ele?") também é pulada: o assunto dela é o de antes, que o laço acha.
    """
    for m in reversed(history):
        if m.get("role") == "user" and m.get("intent") and _states_subject(m.get("content") or ""):
            return m["intent"]
    return None


def _clause_intent(text: str, history: list[dict]) -> str | None:
    import sys

    return domain.active().clause_intent(text, history, sys.modules[__name__])


def classify_offline(text: str, history: list[dict]) -> RouterDecision:
    """Uma intenção por cláusula; o domínio junta as cláusulas (`combine`): intenções diferentes viram `multi`."""
    d = domain.active()
    lang = detect_language(text)
    intents: set[str] = set()
    for clause in split_clauses(text):
        found = _clause_intent(clause, history)
        intents |= set(d.MULTI_PARTS) if found == "multi" else ({found} if found else set())
    intent, conf = d.combine(intents or {d.DEFAULT_INTENT})
    return RouterDecision(intent=intent, plan=d.PLANS[intent], language=lang, confidence=conf)


def few_shot(text: str, k: int = FEW_SHOT_K) -> list[tuple[str, str]]:
    """Os k exemplos rotulados mais parecidos (mesmo k-NN, com multi), do MENOS ao MAIS parecido.

    O mais parecido vai por último, colado na mensagem: é o que o modelo pesa mais (efeito de recência).
    """
    knn = get_knn(clause_level=False)
    return [knn.examples[i] for _, i in reversed(knn.neighbors(text, k))]


def _llm_input(text: str, history: list[dict]) -> str:
    parts = []
    if topic := topic_intent(history):  # a conversa inteira, resumida: o 1º turno vale mesmo fora das 4 recentes
        parts.append(f"Assunto da conversa (última fala com assunto): {topic}")
    recent = "\n".join(f"{m['role']}: {m['content'][:200]}" for m in history[-4:])
    parts.append(f"Histórico recente:\n{recent or '(vazio)'}")
    if shots := few_shot(text):
        lines = "\n".join(f"- {json.dumps(t, ensure_ascii=False)} -> {i}" for t, i in shots)
        parts.append(f"Exemplos rotulados (do menos ao mais parecido com a mensagem):\n{lines}")
    parts.append(f"Mensagem: {text}")
    return "\n\n".join(parts)


def route(text: str, history: list[dict]) -> RouterDecision:
    llm = get_llm("router")
    if not llm.enabled:
        return classify_offline(text, history)
    try:
        raw = llm.complete(domain.prompt("router.md"), [{"role": "user", "content": _llm_input(text, history)}],
                           max_tokens=2048, schema=schema()).json()
        raw["confidence"] = min(1.0, max(0.0, float(raw["confidence"])))
        decision = RouterDecision(**raw)
        if decision.intent not in _plans():
            raise ValueError(f"intent fora do contrato: {decision.intent}")
        return decision
    except (LLMError, ValueError, KeyError, TypeError):  # pydantic.ValidationError é ValueError
        METRICS.inc("router_fallback_total")
        return classify_offline(text, history)


def __getattr__(attr: str):
    # compatibilidade: `router.PLANS` e `router.SCHEMA` refletem o domínio ativo
    if attr == "PLANS":
        return _plans()
    if attr == "SCHEMA":
        return schema()
    raise AttributeError(attr)
