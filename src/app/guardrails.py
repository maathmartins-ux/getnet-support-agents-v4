"""Guardrails em duas camadas.

1. Regex (sempre, custo zero): prompt injection pt/en, pedido de dado de outro usuário, abuso;
   PII (cartão com Luhn, CPF, e-mail, telefone) mascarada antes de qualquer agente, log ou LLM.
2. Classificador (papel "guardrail": Haiku 5.5, effort low, JSON schema) quando há LLM: pega o que a
   regex não vê (paráfrase, outro idioma, texto codificado). Falha do classificador não bloqueia — a
   camada 1 já rodou. Meça falso positivo e falso negativo no split `guardrail` do eval ao vivo.

`mask_output` repete a máscara na resposta: o que sai também não vaza PII.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from . import domain
from .llm import LLMError, get_llm
from .observability import METRICS

_INJECTION = re.compile(
    r"ignore (all |the |your )?(previous|above|prior)? ?(instructions|rules)"
    r"|ignor[ea] (as |suas )?(instru[cç][oõ]es|regras)"
    r"|esque[cç]a (as |suas |todas as )?(regras|instru[cç][oõ]es)|forget (your|all|the) (rules|instructions)"
    r"|system prompt|prompt do sistema|you are now|voc[eê] agora [eé]|jailbreak"
    # "modo desenvolvedor" só como ORDEM: o site da Getnet tem "evite o modo desenvolvedor" (v3: trecho real
    # descartado como envenenado). Ativar/entrar/ligar + modo desenvolvedor, ou "em modo desenvolvedor".
    r"|\b(enable|activate|enter|turn on|switch (on|to)|ative|ativar|ativa|ligue|ligar|entre|entrar|habilite)\b"
    r"\W+(o |no |em |the )?(developer mode|modo (de )?desenvolvedor)"
    r"|\b(in|em|no) (developer mode|modo (de )?desenvolvedor)\b(?! (do|of) (aparelho|celular|phone|android))",
    re.IGNORECASE,
)
_OTHER_USER = re.compile(r"\b(dados|pedidos?|registros?|data|orders?|records?)\b.{0,40}"
                         r"\b(d[oa] outr[oa] (cliente|usu[aá]rio|pessoa)|of (another|other) (customer|user))\b"
                         r"|\b(outro cliente|outro usu[aá]rio|another customer|another user)\b", re.IGNORECASE)
# Disfarces que passavam pela regex (referência pública do case, 31/10/2026): zero-width, letras espaçadas
# ("i g n o r e", "s-y-s-t-e-m"), l33t ("1gn0re") e full-width (o NFKC resolve). Só a CÓPIA é desofuscada;
# o texto que segue para agente e LLM continua o original mascarado.
_ZERO_WIDTH = re.compile('[\u200b-\u200f\u2060\ufeff\xad]')
# mesmo separador do começo ao fim (\1): "o s-y-s-t-e-m" não vira "osystem", só "system"
_SPACED = re.compile(r"\b\w([ .\-_*])\w(?:\1\w)+\b")
_LEET = str.maketrans("013457@$", "oieastas")
_ABUSE = re.compile(r"\b(idiota|imbecil|vai se f\w*|fdp|stupid bot|fuck you)\b", re.IGNORECASE)
# 13–19 dígitos cobre as bandeiras ISO/IEC 7812; Luhn evita mascarar protocolo ou pedido.
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?55\s?)?\(?\d{2}\)?\s?9\d{4}-?\d{4}(?!\d)")

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["category"],
          "properties": {"category": {"type": "string",
                                      "enum": ["safe", "prompt_injection", "other_user_data", "abusive"]}}}
BLOCKING = {"prompt_injection", "other_user_data", "abusive"}


def _luhn_ok(number: str) -> bool:
    digits = [int(d) for d in number if d.isdigit()]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def mask_pii(text: str, keep: frozenset[str] = frozenset()) -> tuple[str, list[str]]:
    """`keep`: e-mails/telefones públicos (contato oficial citado da base) que não são mascarados."""
    flags: list[str] = []

    def card(m: re.Match) -> str:
        if _luhn_ok(m.group()):
            flags.append("pii_card")
            return "[CARTÃO]"
        return m.group()

    text = _CARD.sub(card, text)
    for name, rx, label in (("pii_cpf", _CPF, "[CPF]"), ("pii_email", _EMAIL, "[EMAIL]"),
                            ("pii_phone", _PHONE, "[TELEFONE]")):
        if any(m.group() not in keep for m in rx.finditer(text)):
            flags.append(name)
            text = rx.sub(lambda m, label=label: m.group() if m.group() in keep else label, text)
    return text, flags


def mask_output(text: str, public: str = "") -> str:
    """Mascara PII da resposta, menos o contato que aparece literalmente em `public` (texto das fontes citadas).

    Smoke ao vivo de 31/10/2026: o e-mail oficial da FAQ saía como "[EMAIL]" na resposta.
    """
    keep = frozenset(m.group() for rx in (_EMAIL, _PHONE) for m in rx.finditer(public))
    return mask_pii(text, keep)[0]


@dataclass
class Verdict:
    text: str
    blocked: bool = False
    reason: str | None = None
    flags: list[str] = field(default_factory=list)


def deobfuscate(text: str) -> str:
    text = _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text))
    text = _SPACED.sub(lambda m: m.group().replace(m.group(1), ""), text)
    return text.translate(_LEET)


def looks_like_injection(text: str) -> bool:
    """Também usada na carga da base: documento com instrução embutida é descartado (injeção indireta)."""
    return bool(_INJECTION.search(text) or _INJECTION.search(deobfuscate(text)))


def _regex_layer(message: str) -> str | None:
    plain = deobfuscate(message)
    for reason, rx in (("prompt_injection", _INJECTION), ("other_user_data", _OTHER_USER), ("abusive", _ABUSE)):
        if rx.search(message) or rx.search(plain):
            return reason
    return None


def _llm_layer(masked: str) -> str | None:
    llm = get_llm("guardrail")
    if not llm.enabled:
        return None
    try:
        category = llm.complete(domain.prompt("guardrail.md"), [{"role": "user", "content": masked}], max_tokens=2048,
                                schema=SCHEMA).json()["category"]
    except (LLMError, ValueError, KeyError, TypeError):
        METRICS.inc("guardrail_llm_unavailable_total")
        return None  # camada 1 já rodou; indisponibilidade do classificador não derruba o atendimento
    return category if category in BLOCKING else None


def pre_check(message: str) -> Verdict:
    """Camada 1 (regex + máscara de PII). Rápida: roda antes de tudo."""
    reason = _regex_layer(message)
    if reason:
        return Verdict(text="", blocked=True, reason=reason, flags=[reason, "layer_regex"])
    masked, flags = mask_pii(message)
    return Verdict(text=masked, flags=flags)


def llm_check(verdict: Verdict) -> Verdict:
    """Camada 2 sobre o texto JÁ mascarado. O pipeline a roda em paralelo com o router (latência)."""
    if verdict.blocked:
        return verdict
    reason = _llm_layer(verdict.text)
    if reason:
        return Verdict(text="", blocked=True, reason=reason, flags=[*verdict.flags, reason, "layer_llm"])
    return verdict


def check_input(message: str) -> Verdict:
    return llm_check(pre_check(message))
