"""Checagem numérica determinística da resposta: todo número precisa aparecer na evidência ou na pergunta.

Evidência = texto das fontes citadas, resultado bruto das tools e textos de template dos agentes. A checagem não
usa LLM: pega preço, prazo ou valor inventado mesmo quando o juiz aprova a resposta. Ela aponta números a
conferir; uma soma correta feita pelo modelo também aparece, e isso é dito no README, não escondido.

Formatos equivalentes viram o mesmo número: "R$ 79,90" = "79.9", "1.234,56" = "1234.56", "2,5%" = "2.5".
Com vírgula E ponto, o último separador é o decimal: "5,000.00" (en) = "5.000,00" (pt) = 5000. Só vírgula em
grupos de 3 ("5,000") vale pelas duas leituras (5 e 5000): o idioma da resposta não diz qual é.
Datas são um número só: "30/10/2026" = "2026-10-30"; "03/11" casa com qualquer data da evidência no mesmo dia
e mês; "10/30/2026" (mm/dd) vale quando a ordem só tem uma leitura possível. Dia, mês e ano de uma data da
evidência também contam sozinhos ("Oct 30"). Escala por extenso: "5 mil" vale 5 ou 5000. Número só por
extenso ("cinco mil") não tem dígito e não é conferido: a checagem nunca marca o que não está em dígitos.
Não são número: marcadores de citação "[1]", itens de lista "1." / "2)" no início da linha e dígitos colados
em letras (protocolo "T-f70e7d6e", id "a1b2c3"). "9h" e "3x" contam como 9 e 3.
Getnet v3 (31/10/2026): data dd/mm contra ISO e "R$ 5,000.00" eram os falsos positivos do eval ao vivo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_CITATION = re.compile(r"\[\d+\]")
_LIST_MARK = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)
# dígitos não colados em letra/hífen antes; depois, nem letra/hífen, exceto um sufixo h, x ou %
# "R$ 80mil" (sem espaço, como no site da Getnet) também é número: a escala colada entra pelo lookahead
_NUM = re.compile(r"(?<![\w-])\d+(?:[.,]\d+)*(?:(?![\w-])|(?=[hx%](?![\w]))"
                  r"|(?=(?:mil|milh|bilh|thousand|million|billion)\w*\b))", re.IGNORECASE)
_THOUSANDS = re.compile(r"\d{1,3}(?:\.\d{3})+")
_EN_THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+")
_ISO_DATE = re.compile(r"(?<![\w-])(\d{4})-(\d{1,2})-(\d{1,2})(?=T\d|[^\w-]|$)")
_SLASH_DATE = re.compile(r"(?<![\w/])(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?(?![\w/])")
_SCALE = re.compile(r"\s*(mil|milh(?:ão|ões|ao|oes)|bilh(?:ão|ões|ao|oes)|thousand|million|billion)\b",
                    re.IGNORECASE)
_FULL_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# por prefixo, do mais longo ao mais curto: "milhões" começa com "mil" e não é mil
_SCALES = {"milh": 10**6, "million": 10**6, "bilh": 10**9, "billion": 10**9, "mil": 1000, "thousand": 1000}


@dataclass
class _Item:
    raw: str  # como está escrito no texto (é o que a checagem devolve)
    values: set[str]  # leituras possíveis; basta uma estar na evidência
    parts: tuple[str, ...] = ()  # dia e mês de "dd/mm" sem ano: soltos na evidência também sustentam


def _decimal(token: str) -> str | None:
    try:
        return format(Decimal(token).normalize(), "f")
    except InvalidOperation:
        return None


def _readings(token: str) -> set[str]:
    if "," in token and "." in token:  # os dois: o último separador é o decimal
        decimal_sep = "," if token.rfind(",") > token.rfind(".") else "."
        thousands = "." if decimal_sep == "," else ","
        cands = [token.replace(thousands, "").replace(decimal_sep, ".")]
    elif "," in token:  # pt-BR: vírgula é decimal; "5,000" também pode ser milhar en
        cands = [token.replace(",", ".")]
        if _EN_THOUSANDS.fullmatch(token):
            cands.append(token.replace(",", ""))
    elif _THOUSANDS.fullmatch(token):
        cands = [token.replace(".", "")]
    else:
        cands = [token]
    return {v for v in map(_decimal, cands) if v is not None}


def _date(year: int, month: int, day: int) -> str | None:
    return f"{year:04d}-{month:02d}-{day:02d}" if 1 <= month <= 12 and 1 <= day <= 31 else None


def _slash_values(a: str, b: str, year: str | None) -> set[str]:
    y = None if year is None else int(year) + (2000 if len(year) == 2 else 0)
    out = set()
    for day, month in ((int(a), int(b)), (int(b), int(a))):  # dd/mm (pt) e mm/dd (en); data impossível cai
        if 1 <= month <= 12 and 1 <= day <= 31:
            out.add(_date(y, month, day) if y is not None else f"--{month:02d}-{day:02d}")
    return out


def _items(text: str) -> list[_Item]:
    text = _LIST_MARK.sub(" ", _CITATION.sub(" ", text))
    items: list[_Item] = []

    def take_date(m: re.Match, values: set[str]) -> str:
        # "24/7" ou "1/3" também casam o padrão sem ano: aí dia e mês soltos na evidência bastam (regra antiga).
        # Data com ano só vale inteira: 30, 10 e 2026 vindos de datas diferentes não sustentam "30/10/2026".
        no_year = m.re is _SLASH_DATE and m.group(3) is None
        parts = tuple(_decimal(g) for g in m.groups()[:2]) if no_year else ()
        items.append(_Item(m.group(0), values, parts))
        return " " * len(m.group(0))  # some da busca de número, sem colar vizinhos

    def iso(m: re.Match) -> str:
        full = _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return take_date(m, {full} if full else set())

    text = _ISO_DATE.sub(iso, text)
    text = _SLASH_DATE.sub(lambda m: take_date(m, _slash_values(*m.groups())), text)
    for m in _NUM.finditer(text):
        values = _readings(m.group(0))
        scale = _SCALE.match(text, m.end())
        if scale and values:
            word = scale.group(1).lower()
            mult = next(v for k, v in _SCALES.items() if word.startswith(k))
            values |= {_decimal(str(Decimal(v) * mult)) for v in values} - {None}
        items.append(_Item(m.group(0), values))
    return items


def numbers(text: str) -> set[str]:
    """Tudo o que o texto sustenta: leituras dos números e, de cada data inteira, "--mm-dd", dia, mês e ano."""
    out: set[str] = set()
    for item in _items(text):
        out |= item.values
        out.update(p for p in item.parts if p is not None)
        for v in item.values:
            if _FULL_DATE.fullmatch(v):
                year, month, day = v.split("-")
                out |= {f"--{month}-{day}", *(_decimal(x) for x in (year, month, day))}
    return out


def unsupported_numbers(answer: str, evidence: list[str], question: str = "") -> list[str]:
    """Números da resposta que não aparecem na evidência nem na pergunta, como escritos na resposta, sem repetir."""
    known = numbers(question).union(*(numbers(e) for e in evidence))
    out: list[str] = []
    for item in _items(answer):
        if not item.values and not item.parts:
            continue
        supported = bool(item.values & known) or (bool(item.parts) and set(item.parts) <= known)
        if not supported and item.raw not in out:
            out.append(item.raw)
    return out


# --- risco de fintech na saída: promessa e compromisso que a fonte não sustenta ------------------------------------
# Espelha a solução pública "Buzz" (Agent Swarm): promessa financeira, compromisso legal e garantia são os erros mais
# caros no atendimento de pagamentos. Sinaliza e mede (como os números): bloquear derrubaria resposta certa.
_RISK = {
    "promessa_financeira": re.compile(
        r"\b(garant(o|imos|ido|ida|ia)|com certeza (vai|cai|ser[áa])|sem (nenhum )?risco|100% (seguro|garantido)"
        r"|guarantee[ds]?|you will (definitely|certainly)|risk[- ]free|we promise|prometemos)\b", re.IGNORECASE),
    "compromisso_legal": re.compile(
        r"\b(isento de (multa|tarifa|responsabilidade)|n[ãa]o (h[áa]|haver[áa]) cobran[çc]a|reembolso integral"
        r"|full refund|no fees? (will|ever)|legalmente obrigad[oa]|legally (required|bound))\b", re.IGNORECASE),
}


def risk_flags(answer: str, evidence: list[str]) -> list[str]:
    """Rótulos de risco cuja frase aparece na resposta e NÃO aparece na evidência do turno. [] = nada a revisar."""
    support = " ".join(evidence)
    out = []
    for label, pattern in _RISK.items():
        said = {m.group(0).lower() for m in pattern.finditer(answer)}
        backed = {m.group(0).lower() for m in pattern.finditer(support)}
        if said - backed:
            out.append(label)
    return out
