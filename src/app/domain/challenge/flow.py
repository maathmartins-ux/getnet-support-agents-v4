"""Domínio do desafio — FLUXO (frente R): taxonomia Getnet, planos, FALLBACK, regras do router offline.

Contrato lido pelo motor: PLANS, FALLBACK, ALLOWED, DEPENDS, NO_ESCALATION, DEFAULT_INTENT,
MULTI_PARTS, states_subject(), clause_intent(), combine().
Teste: tests/test_domain_router.py e tests/test_domain_supervisor.py. Taxonomia e desempates: docs/SPEC.md.
"""
from __future__ import annotations

import re

# --- taxonomia e planos (lidos por router e supervisor) ---------------------------------------------------
# intent → plano inicial. faq = Knowledge Agent (RAG + web), records = Customer Support Agent (tools do lojista).
PLANS = {
    "knowledge": ["faq"],
    "general": ["faq"],  # faq com busca web; nunca escala (NO_ESCALATION)
    "support": ["records"],
    "troubleshoot": ["records", "faq"],  # estado do terminal/transação primeiro, depois o manual
    "multi": ["faq", "records"],  # duas perguntas independentes → paralelo
    "human_request": ["escalation"],
}
# Status != ok → próximo agente. Tool quebrada → knowledge; sem fonte → humano.
FALLBACK = {"faq": "escalation", "records": "faq"}
# Transições permitidas (de → para). None = início. Escalation é terminal.
ALLOWED = {None: {"faq", "records", "escalation"}, "faq": {"records", "escalation"},
           "records": {"faq", "escalation"}, "escalation": set()}
# troubleshoot: o manual (faq) lê o estado do terminal/transação que records trouxe → não rodam no mesmo lote.
DEPENDS: dict[str, set[str]] = {"faq": {"records"}}
# clima, câmbio e notícia nunca abrem caso humano: sem web, o agente diz que não sabe.
NO_ESCALATION: set[str] = {"general"}
DEFAULT_INTENT = "knowledge"  # cláusula com assunto do domínio e sem regra
MULTI_PARTS = {"knowledge", "support"}  # o que uma frase "multi" soma às intenções da mensagem

# --- regras do router offline -------------------------------------------------------------------------------
_I = re.IGNORECASE
_HUMAN = re.compile(r"\b(atendente|humano|human|pessoa (real|de verdade)|falar com algu[eé]m|representative"
                    r"|real person|operador|operator|live agent|talk to (an? )?(agent|person|someone)"
                    r"|speak (to|with) (an? )?(agent|person|someone)|ger[eê]ncia da getnet)\b", _I)
# Menção à Getnet ou a produto/assunto dela: tira a pergunta de `general` (câmbio NA Getnet é knowledge).
_GETNET = re.compile(r"\b(getnet|maquininha\w*|card machine|terminal|get ?smart|get ?cl[aá]ssica|get ?tap|pix"
                     r"|link de pagamento|payment link|credi[aá]rio|antecip\w*|anticipat\w*|lojista\w*|merchant\w*"
                     r"|vend[aei]\w*|sales?|receb\w*|receivables?|taxas?|fees?|parcel\w*|installments?"
                     r"|bancos?|banks?|ag[eê]ncia)\b", _I)
_GENERAL = re.compile(r"\b(clima|tempo (amanh[aã]|hoje|em)|previs[aã]o do tempo|temperatura|vai chover|chuva"
                      r"|weather|forecast|temperature|rain\w*|c[aâ]mbio|cota[cç][aã]o|exchange rate|d[oó]lar"
                      r"|dollar|euro|libra|bitcoin|not[ií]cias?|news|headlines?|manchete\w*|placar|score)\b", _I)
# Pedido de LISTA de transações dele ("quais minhas foram recusadas", "list my declined") é dado, não sintoma.
_DATA_LIST = re.compile(r"\b(list|show|liste|listar|mostr\w+|quais|which)\b.*\b(transa[cç]\w+|transactions?"
                        r"|vendas|sales|declined|recusad\w+)\b", _I)
# Sintoma na maquininha ou numa transação → troubleshoot, mesmo com "minha maquininha".
_SYMPTOM = re.compile(r"(n[aã]o (conecta|liga|funciona|imprime|l[eê]|passa|aceita)\w*|won'?t (connect|turn on|work"
                      r"|print|read)|can'?t connect|cannot connect|not (connecting|working|printing|reading)"
                      r"|\berr(o|os|or|ors)\b|travou|travad[ao]|congel\w+|froze|frozen|freez\w+|stuck|recusad\w+"
                      r"|declin\w+|negad[ao]|sem sinal|no signal|sem conex[aã]o|no connection|offline|desligando"
                      r"|keeps? (turning off|restarting)|falh(a|ou)|failed|failing|reject\w*|rejeitad\w+)", _I)
# Dado do PRÓPRIO lojista ou ação dele (reinicie/conteste/antecipe) → support, venha o que vier.
_DATA = re.compile(r"(quando cai|quando (vai )?cair|quando (eu )?recebo|when will .*\b(deposit|paid|pay|arrive"
                   r"|receive)\w*|deposited|quanto (eu )?(vendi|recebi|tenho)|how much (did|have) i (sell|sold|receiv)"
                   r"|how much i (sold|made)|\bmeu saldo|\bsaldo\b|\bmy balance|\bbalance to receive"
                   r"|dispon[ií]vel (para|pra) antecipar|available (to|for) (anticipat|advance)\w*"
                   r"|(quero|gostaria de|preciso) antecipar|\bantecipe\b|request (an? )?(anticipation|advance)"
                   r"|anticipate my|advance my|reinici\w+|\brestart\w*|\breboot\w*|contesta\w*|contest\w*"
                   r"|chargeback|dispute (the|a|my)|\bTX\d+|\bT\d{4}[A-Z]?\b|vend\w+ de ontem|yesterday'?s sales"
                   r"|sales (from|of) (yesterday|today|last)|vendi (ontem|hoje)|meus recebimentos|my (recent )?"
                   r"(transactions|receivables|settlements|deposits|payments)|minhas transa[cç]\w+|meu terminal"
                   r"|my terminal|status d[ao] minha|extrato)", _I)
# Possessivo fraco ("my sales", "minhas vendas"): support só se não for pergunta de produto (AC-04).
_WEAK_DATA = re.compile(r"\b(my sales|minhas vendas|my money|meu dinheiro|my machine|minha maquininha)\b", _I)
# Pergunta sobre produto/regra sem pedir dado: "preciso de / como funciona / posso / o que é".
_KNOWLEDGE = re.compile(r"\b(preciso (de|ter)|como funciona|como (eu )?(fa[cç]o|recebo|uso|vendo)|posso|pode|o que [eé]"
                        r"|qual a diferen[cç]a|diferen[cç]a|quantas|quanto custa|tem como|existe|aceita"
                        r"|do i need|how does|how do|how can|can i|is it possible|what is|what's the difference"
                        r"|what are|difference|explain|tell me about|how many|does getnet|do you)\b", _I)
_FOLLOWUP = re.compile(r"^\W*(e|and|mas|but)?\W*(ele|ela|isso|esse|essa|esse a[ií]|o outro|a outra|it|that|that one"
                       r"|this one|the other one|e a[ií]|and then|e agora|and now)\b", _I)
# Duas perguntas na mesma cláusula ("How does X work, and when will ...?") — o motor só corta por pontuação.
_SUBPART = re.compile(r",\s*(?:and also|and|e também|e tamb[eé]m|e)\s+|\s+and also\s+|\s+e também\s+", _I)


def states_subject(text: str) -> bool:
    """A fala define assunto por REGRA (sem olhar vocabulário)? Usado para achar o assunto da conversa."""
    return any(p.search(text) for p in (_HUMAN, _GENERAL, _SYMPTOM, _DATA, _WEAK_DATA))


def _rule_intent(text: str) -> str | None:
    """Regras na ordem dos desempates da SPEC. None = nenhuma regra decide."""
    if _HUMAN.search(text):
        return "human_request"
    if _GENERAL.search(text) and not _GETNET.search(text):
        return "general"
    if _DATA_LIST.search(text) and not re.search(r"\b(erro|error|por que|why)\b", text, _I):
        return "support"
    if _SYMPTOM.search(text):
        return "troubleshoot"
    if _DATA.search(text):
        return "support"
    if _WEAK_DATA.search(text) and not _KNOWLEDGE.search(text):
        return "support"
    return None


def clause_intent(text: str, history: list[dict], r) -> str | None:
    """Intenção de UMA cláusula. `r` = módulo app.router (topic_intent, knn_intent, has_subject)."""
    parts = [p for p in _SUBPART.split(text) if p and p.strip()]
    if len(parts) > 1:  # "Explain the Get Smart, and also show my recent transactions."
        found = {i for p in parts if (i := _single(p, history, r))}
        if found:
            intent, _ = combine(found)
            return intent
    return _single(text, history, r)


def _single(text: str, history: list[dict], r) -> str | None:
    if (rule := _rule_intent(text)) is not None:
        return rule
    last = r.topic_intent(history)
    if last and _FOLLOWUP.search(text) and len(text.split()) <= 6:  # memória: "e ele?" herda o assunto
        return last
    if _KNOWLEDGE.search(text) or _GETNET.search(text):  # produto/regra da Getnet sem pedir dado
        return "knowledge"
    # Nenhuma regra casou: o k-NN dos exemplos rotulados generaliza a paráfrase.
    if (near := r.knn_intent(text)) is not None:
        return near
    # Conversa sem vocabulário do domínio ("Olá!") não vira pergunta: o motor cai no DEFAULT_INTENT só se nada sobrar.
    return DEFAULT_INTENT if r.has_subject(text) else None


_SPECIFIC = ("support", "knowledge", "general")  # sem multi/troubleshoot/humano: a mais específica vence


def combine(intents: set[str]) -> tuple[str, float]:
    """Intenções das cláusulas → (intent da mensagem, confiança)."""
    if "human_request" in intents:
        return "human_request", 0.95
    if "troubleshoot" in intents:  # o plano records → faq já cobre a parte de dado e a de manual
        return "troubleshoot", 0.85
    if "multi" in intents or ("support" in intents and intents & {"knowledge", "general"}):
        return "multi", 0.8
    for intent in _SPECIFIC:
        if intent in intents:
            return intent, {"support": 0.85, "knowledge": 0.75, "general": 0.8}[intent]
    return DEFAULT_INTENT, 0.5
