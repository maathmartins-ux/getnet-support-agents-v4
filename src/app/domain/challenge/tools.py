"""Domínio do desafio — FERRAMENTAS do Customer Support Agent (frente T).

Contrato lido pelo motor: TOOLS, ELIGIBLE_STATUS, records(), records_offline(). Teste em tests/test_tools.py.
6 tools (nomes congelados na SPEC): 3 de leitura e 3 de escrita (escrita só vira pending_action; risco alto
exige confirmação do dono E aprovação de operador, ver actions.py). Identidade sempre do servidor (`user_id`
injetado por run_tool); inexistente e de outro dono dão a MESMA resposta (not_found).
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from functools import lru_cache

from app.config import data_dir

# --- sistema de registro (adaptador do CRM) -------------------------------------------------------------------


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict:
    return json.loads(open(path, encoding="utf-8").read())


def records() -> dict:
    """Adaptador do CRM: hoje data/crm/merchants.json; em produção, a API do CRM (mesma assinatura)."""
    path = data_dir() / "crm" / "merchants.json"
    return _load(str(path), path.stat().st_mtime)


def _merchant(user_id: str) -> dict | None:
    m = records().get(user_id)
    return m if isinstance(m, dict) and not user_id.startswith("_") else None


# --- calendário de liquidação (dias úteis) --------------------------------------------------------------------
# Feriados nacionais fixos (mm-dd). Móveis (Carnaval, Sexta-feira Santa, Corpus Christi) ficam fora: o CRM real
# traria o calendário da câmara de liquidação.
HOLIDAYS = {"01-01", "04-21", "05-01", "09-07", "10-12", "11-02", "11-15", "11-20", "12-25"}
# modalidade -> (dias, tipo): Pix D0 e débito D+1 em dias úteis; crédito D+30 corridos, ajustado ao dia útil
SETTLEMENT_RULE = {"pix": (0, "uteis"), "debito": (1, "uteis"), "credito": (30, "corridos")}


def business_day(d: date) -> bool:
    return d.weekday() < 5 and d.strftime("%m-%d") not in HOLIDAYS


def next_business_day(d: date) -> date:
    while not business_day(d):
        d += timedelta(days=1)
    return d


def settlement_date(sale: date, method: str) -> date:
    days, kind = SETTLEMENT_RULE[method]
    if kind == "corridos":
        return next_business_day(sale + timedelta(days=days))
    if days == 0:  # D0: mesmo dia se útil; venda no fim de semana/feriado cai no próximo dia útil
        return next_business_day(sale)
    d = sale
    for _ in range(days):  # D+N = N-ésimo dia útil depois da venda
        d = next_business_day(d + timedelta(days=1))
    return d


def _today() -> date:
    return date.today()


def _tx_view(tx: dict, today: date) -> dict:
    sale = today - timedelta(days=int(tx["days_ago"]))
    out = {"transaction_id": tx["id"], "date": sale.isoformat(), "method": tx["method"], "amount": tx["amount"],
           "status": tx["status"], "terminal_id": tx.get("terminal")}
    if tx["status"] == "recusada":
        out.update(decline_code=tx.get("code"), decline_reason=tx.get("reason"))
    out["contestable"] = tx["status"] == "aprovada"
    return out


# --- leitura ------------------------------------------------------------------------------------------------
_NOT_FOUND = {"ok": False, "error": "not_found"}


def _get_settlements(user_id: str, period: str = "upcoming") -> dict:
    m = _merchant(user_id)
    if m is None:
        return _NOT_FOUND
    today = _today()
    items = []
    for tx in m["transactions"]:
        if tx["status"] != "aprovada":
            continue
        sale = today - timedelta(days=int(tx["days_ago"]))
        pay = settlement_date(sale, tx["method"])
        item = {"transaction_id": tx["id"], "sale_date": sale.isoformat(), "method": tx["method"],
                "amount": tx["amount"], "expected_date": pay.isoformat(),
                "status": "liquidado" if pay <= today else "previsto"}
        keep = {"yesterday": tx["days_ago"] == 1, "today": tx["days_ago"] == 0,
                "last_7_days": tx["days_ago"] <= 7, "upcoming": pay > today}.get(period, True)
        if keep:
            items.append(item)
    by_method: dict[str, dict] = {}
    for it in items:
        g = by_method.setdefault(it["method"], {"amount": 0.0, "expected_dates": []})
        g["amount"] = round(g["amount"] + it["amount"], 2)
        if it["expected_date"] not in g["expected_dates"]:
            g["expected_dates"].append(it["expected_date"])
    # disponível p/ antecipar = crédito ainda não liquidado (independe do filtro de período)
    available = round(sum(tx["amount"] for tx in m["transactions"] if tx["status"] == "aprovada"
                          and tx["method"] == "credito"
                          and settlement_date(today - timedelta(days=int(tx["days_ago"])), "credito") > today), 2)
    return {"ok": True, "data": {"today": today.isoformat(), "period": period, "items": items,
                                 "by_method": by_method, "total": round(sum(i["amount"] for i in items), 2),
                                 "available_for_anticipation": available}}


def _get_transactions(user_id: str, status: str | None = None, transaction_id: str | None = None) -> dict:
    m = _merchant(user_id)
    if m is None:
        return _NOT_FOUND
    today = _today()
    txs = [_tx_view(t, today) for t in sorted(m["transactions"], key=lambda t: -int(t["days_ago"]))]
    if transaction_id:
        txs = [t for t in txs if t["transaction_id"] == transaction_id]
        if not txs:  # inexistente ou de outro lojista: mesma resposta
            return _NOT_FOUND
    if status:
        txs = [t for t in txs if t["status"] == status]
    approved = [t for t in txs if t["status"] == "aprovada"]
    return {"ok": True, "data": {"count": len(txs), "approved_count": len(approved),
                                 "approved_total": round(sum(t["amount"] for t in approved), 2),
                                 "declined_count": len(txs) - len(approved), "transactions": txs}}


def _terminal(user_id: str, terminal_id: str) -> dict | None:
    m = _merchant(user_id)
    return next((t for t in (m or {}).get("terminals", []) if t["id"] == terminal_id), None)


def _get_terminal_status(user_id: str, terminal_id: str | None = None) -> dict:
    m = _merchant(user_id)
    if m is None:
        return _NOT_FOUND
    terms = m["terminals"]
    if terminal_id:
        terms = [t for t in terms if t["id"] == terminal_id]
        if not terms:
            return _NOT_FOUND
    out = [{"terminal_id": t["id"], "model": t["model"], "connection": t["connection"], "signal": t["signal"],
            "online": t["online"], "last_contact_min": t["last_contact_min"], "paper_roll": t["paper_roll"],
            "blocked": t["blocked"]} for t in terms]
    return {"ok": True, "data": {"terminals": out}}


# --- escrita (só roda depois da confirmação; antes disso run_tool devolve pending_action) -------------------
def _restart_terminal(user_id: str, terminal_id: str) -> dict:
    if _terminal(user_id, terminal_id) is None:
        return _NOT_FOUND
    return {"ok": True, "data": {"terminal_id": terminal_id, "status": "restart_sent"}}


def _request_anticipation(user_id: str, amount: float | None = None) -> dict:
    found = _get_settlements(user_id)
    if not found["ok"]:
        return found
    available = found["data"]["available_for_anticipation"]
    value = available if amount is None else round(float(amount), 2)
    if value <= 0 or value > available:
        return {"ok": False, "error": "amount_exceeds_available", "available_for_anticipation": available}
    return {"ok": True, "data": {"amount": value, "status": "anticipation_requested"}}


def _open_chargeback(user_id: str, transaction_id: str, reason: str | None = None) -> dict:
    found = _get_transactions(user_id, transaction_id=transaction_id)
    if not found["ok"]:
        return found
    tx = found["data"]["transactions"][0]
    if tx["status"] not in ELIGIBLE_STATUS["open_chargeback"]:  # recusada não tem o que contestar
        return {"ok": False, "error": "not_eligible", "status": tx["status"]}
    return {"ok": True, "data": {"transaction_id": transaction_id, "status": "chargeback_opened"}}


_TX_ID = {"type": "string", "pattern": r"^TX\d{4}$", "description": "Id da transação (ex.: TX1004)"}
_TERM_ID = {"type": "string", "pattern": r"^T\d{4}[A-Z]$", "description": "Id da maquininha (ex.: T1988B)"}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


TOOLS = {
    "get_settlements": {
        "description": "Agenda de recebimentos do lojista autenticado: cada venda aprovada com valor, modalidade, "
                       "data prevista de depósito em dias úteis (Pix no mesmo dia, débito no dia útil seguinte, "
                       "crédito em 30 dias), status, total e valor disponível para antecipar.",
        "write": False, "fn": _get_settlements,
        "input_schema": _obj({"period": {"type": "string", "enum": ["yesterday", "today", "last_7_days", "upcoming"],
                                         "description": "Vendas de ontem, de hoje, dos últimos 7 dias ou tudo "
                                                        "ainda a receber (padrão)."}})},
    "get_transactions": {
        "description": "Transações recentes do lojista autenticado (aprovadas e recusadas), com código e motivo "
                       "da recusa e se a transação é contestável.",
        "write": False, "fn": _get_transactions,
        "input_schema": _obj({"status": {"type": "string", "enum": ["aprovada", "recusada"]},
                              "transaction_id": _TX_ID})},
    "get_terminal_status": {
        "description": "Maquininhas do lojista autenticado: modelo, conexão (chip/Wi-Fi), sinal, online, minutos "
                       "desde o último contato, bobina e bloqueio.",
        "write": False, "fn": _get_terminal_status, "input_schema": _obj({"terminal_id": _TERM_ID})},
    "restart_terminal": {
        "description": "Reinicia remotamente uma maquininha do lojista. Escrita: só propõe; executa depois que "
                       "o lojista confirmar.",
        "write": True, "fn": _restart_terminal, "input_schema": _obj({"terminal_id": _TERM_ID}, ["terminal_id"])},
    # risco alto: move dinheiro -> confirmação do dono E aprovação de um operador (actions.py)
    "request_anticipation": {
        "description": "Solicita antecipação de recebíveis de crédito. Sem amount = todo o disponível. Exige "
                       "confirmação do lojista e aprovação de um atendente.",
        "write": True, "risk": "high", "fn": _request_anticipation,
        "input_schema": _obj({"amount": {"type": "number", "exclusiveMinimum": 0,
                                         "description": "Valor em reais; omita para antecipar tudo."}})},
    "open_chargeback": {
        "description": "Abre contestação de uma transação APROVADA do lojista (recusada não é contestável). "
                       "Exige confirmação do lojista e aprovação de um atendente.",
        "write": True, "risk": "high", "fn": _open_chargeback,
        "input_schema": _obj({"transaction_id": _TX_ID,
                              "reason": {"type": "string", "maxLength": 200}}, ["transaction_id"])},
}
# Ação de escrita -> status do item em que ela faz sentido (v3: contestava a última transação, mesmo recusada).
ELIGIBLE_STATUS: dict[str, set[str]] = {"open_chargeback": {"aprovada"}}

# --- caminho offline do agente records (sem LLM / provedor fora) -------------------------------------------
_TX_RE = re.compile(r"\b(TX\d{4})\b", re.IGNORECASE)
_TERM_RE = re.compile(r"\b(T\d{4}[A-Z])\b", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)", re.IGNORECASE)
_RESTART = re.compile(r"\b(reinici\w*|reinicie|restart\w*|reboot\w*|religu\w*)", re.IGNORECASE)
_ANTICIP = re.compile(r"\b(antecip\w*|anticipat\w*|advance)", re.IGNORECASE)
_CHARGEBACK = re.compile(r"\b(contest\w*|chargeback|dispute|n[aã]o reconhe\w*)", re.IGNORECASE)
_SETTLE = re.compile(r"\b(dep[oó]sit\w*|cai\b|caem|cair|receb\w*|settle\w*|liquida\w*|deposited|paid out|"
                     r"saldo|balance|quando entra|quanto (?:eu )?vendi|how much did i sell|ontem|yesterday)",
                     re.IGNORECASE)
_DECLINE = re.compile(r"\b(recus\w*|declin\w*|negad\w*)", re.IGNORECASE)
_TX_WORDS = re.compile(r"\b(transa\w*|recus\w*|declin\w*|negad\w*|vendi|venda|vendas|sales?|c[oó]digo)",
                       re.IGNORECASE)
_TERMINAL = re.compile(r"\b(maquininha|m[aá]quina|terminal|pos|conect\w*|connect\w*|internet|wi-?fi|chip|sinal|"
                       r"signal|card machine|offline|travou|frozen)", re.IGNORECASE)
_METHOD = {"pix": ("Pix", "Pix"), "debito": ("débito", "debit"), "credito": ("crédito", "credit")}


def _brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _ddmm(iso: str) -> str:
    return f"{iso[8:10]}/{iso[5:7]}"


def _scan(ctx, rx: re.Pattern) -> str | None:
    """Id citado agora; senão o mais recente da conversa (memória: "e reinicia ela?")."""
    for text in [ctx.text, *[m["content"] for m in reversed(ctx.history)], ctx.conversation_digest]:
        found = rx.findall(text or "")
        if found:
            return found[-1].upper()
    return None


def _amount(text: str) -> float | None:
    m = _AMOUNT_RE.search(text)
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def _pick_tool(text: str) -> str:
    if _RESTART.search(text):
        return "restart_terminal"
    if _CHARGEBACK.search(text):
        return "open_chargeback"
    if _ANTICIP.search(text) and not re.search(r"\b(como funciona|how does|what is|o que [eé])\b", text, re.I):
        return "request_anticipation"
    if _DECLINE.search(text):  # sintoma numa transação: lê as recusas
        return "get_transactions"
    if _SETTLE.search(text):
        return "get_settlements"
    if _TX_WORDS.search(text) or _TX_RE.search(text):
        return "get_transactions"
    if _TERMINAL.search(text) or _TERM_RE.search(text):
        return "get_terminal_status"
    return "get_settlements"


class _Run:
    """Acumula chamadas e evidência (resultado bruto de cada tool: a base dos números da resposta)."""

    def __init__(self, ctx, a):
        self.ctx, self.a, self.calls, self.ev = ctx, a, [], []

    def __call__(self, name: str, args: dict) -> dict:
        result, call = self.a.run_tool(name, args, self.ctx.user_id, self.ctx.conversation_id)
        self.calls.append(call)
        self.ev.append(json.dumps(result, ensure_ascii=False))
        return result

    def done(self, text: str, status: str = "ok", **kw):
        return self.a.AgentResult("records", status, text=text, tool_calls=self.calls, evidence=self.ev, **kw)


def _pending(run: _Run, result: dict, what_pt: str, what_en: str, facts: dict):
    t, ctx = run.a._t, run.ctx
    aid = result["action_id"]
    high = result.get("risk") == "high"
    extra_pt = " Depois da sua confirmação, um atendente aprova antes de executar." if high else ""
    extra_en = " After you confirm, a human agent approves it before it runs." if high else ""
    return run.done(t(ctx, f"Posso {what_pt}. Nada foi feito ainda; confirme a ação {aid}.{extra_pt}",
                      f"I can {what_en}. Nothing has been done yet; confirm action {aid}.{extra_en}"),
                    pending_action=aid, facts={**facts, "pending_action": aid, "risk": result.get("risk")})


def _settlements_text(run: _Run, data: dict, yesterday: bool) -> str:
    t, ctx = run.a._t, run.ctx
    if not data["items"]:
        return t(ctx, "Não há vendas aprovadas nesse período na sua conta.",
                 "There are no approved sales in that period on your account.")
    parts_pt, parts_en = [], []
    for method, g in data["by_method"].items():
        dates = " e ".join(_ddmm(d) for d in g["expected_dates"])
        dates_en = " and ".join(_ddmm(d) for d in g["expected_dates"])
        pt_m, en_m = _METHOD.get(method, (method, method))
        parts_pt.append(f"o {pt_m} ({_brl(g['amount'])}) cai em {dates}")
        parts_en.append(f"the {en_m} part ({_brl(g['amount'])}) arrives on {dates_en}")
    lead_pt = "As vendas de ontem somam" if yesterday else "Você tem a receber"
    lead_en = "Yesterday's sales add up to" if yesterday else "You have to receive"
    return t(ctx, f"{lead_pt} {_brl(data['total'])}: " + "; ".join(parts_pt)
             + ". Os depósitos seguem dias úteis: fim de semana e feriado passam para o próximo dia útil.",
             f"{lead_en} {_brl(data['total'])}: " + "; ".join(parts_en)
             + ". Deposits follow business days: weekends and holidays move to the next business day.")


def records_offline(ctx, a):
    """Atendimento determinístico do agente records. `a` = módulo app.agents (AgentResult, run_tool, _t...)."""
    t = a._t
    run = _Run(ctx, a)
    tool = _pick_tool(ctx.text)

    if tool == "restart_terminal":
        tid = _TERM_RE.search(ctx.text)
        tid = tid.group(1).upper() if tid else None
        st = run("get_terminal_status", {"terminal_id": tid} if tid else {})  # consulta antes de agir
        if not st.get("ok"):
            return run.done(t(ctx, f"Não encontrei a maquininha {tid} na sua conta. Nada foi feito.",
                              f"I couldn't find card machine {tid} on your account. Nothing has been done."),
                            facts={"terminal_not_found": tid})
        terms = st["data"]["terminals"]
        target = next((x for x in terms if x["terminal_id"] == tid), None) or \
            next((x for x in terms if not x["online"]), None) or (terms[0] if len(terms) == 1 else None)
        if target is None:
            ids = ", ".join(x["terminal_id"] for x in terms)
            return run.done(t(ctx, f"Qual maquininha devo reiniciar ({ids})? Nada foi feito ainda.",
                              f"Which card machine should I restart ({ids})? Nothing has been done yet."))
        tid = target["terminal_id"]
        res = run("restart_terminal", {"terminal_id": tid})
        return _pending(run, res, f"reiniciar a maquininha {tid} ({target['model']})",
                        f"restart card machine {tid} ({target['model']})", {"terminal_id": tid})

    if tool == "request_anticipation":
        st = run("get_settlements", {"period": "upcoming"})
        if not st.get("ok"):
            return run.done("", status="error", reason="tool_error")
        avail = st["data"]["available_for_anticipation"]
        want = _amount(ctx.text)
        if avail <= 0:
            return run.done(t(ctx, "Você não tem recebíveis de crédito disponíveis para antecipar agora. "
                                   "Nada foi feito.",
                              "You have no credit receivables available to advance right now. "
                              "Nothing has been done."), facts={"available_for_anticipation": avail})
        if want is not None and want > avail:
            return run.done(t(ctx, f"O disponível para antecipar é {_brl(avail)}, menor que o valor pedido. "
                                   "Nada foi feito; diga um valor até esse limite.",
                              f"The amount available to advance is {_brl(avail)}, less than you asked for. "
                              "Nothing has been done; tell me an amount up to that limit."),
                            facts={"available_for_anticipation": avail})
        res = run("request_anticipation", {"amount": want} if want is not None else {})
        value = want if want is not None else avail
        return _pending(run, res, f"pedir a antecipação de {_brl(value)} (disponível: {_brl(avail)})",
                        f"request an advance of {_brl(value)} (available: {_brl(avail)})",
                        {"available_for_anticipation": avail})

    if tool == "open_chargeback":
        txid = _scan(ctx, _TX_RE)
        if not txid:
            return run.done(t(ctx, "Qual transação você quer contestar? Me diga o código (ex.: TX1004). "
                                   "Nada foi feito ainda.",
                              "Which transaction do you want to dispute? Tell me its id (e.g. TX1004). "
                              "Nothing has been done yet."))
        found = run("get_transactions", {"transaction_id": txid})  # consulta antes de propor
        if not found.get("ok"):
            return run.done(t(ctx, f"Não encontrei a transação {txid} na sua conta. Nada foi feito.",
                              f"I couldn't find transaction {txid} on your account. Nothing has been done."),
                            facts={"transaction_not_found": txid})
        tx = found["data"]["transactions"][0]
        if a.latest_eligible([tx], "open_chargeback") is None:
            return run.done(t(ctx, f"A transação {txid} foi recusada (código {tx.get('decline_code')}): não houve "
                                   "cobrança, então não há o que contestar. Nada foi feito.",
                              f"Transaction {txid} was declined (code {tx.get('decline_code')}): the cardholder "
                              "was not charged, so there is nothing to dispute. Nothing has been done."),
                            facts={"transaction_id": txid, "status": tx["status"], "ineligible": "open_chargeback"})
        res = run("open_chargeback", {"transaction_id": txid})
        return _pending(run, res, f"abrir a contestação da transação {txid} ({_brl(tx['amount'])}, {tx['date'][8:10]}"
                                  f"/{tx['date'][5:7]})",
                        f"open a chargeback for transaction {txid} ({_brl(tx['amount'])}, "
                        f"{tx['date'][8:10]}/{tx['date'][5:7]})", {"transaction_id": txid})

    if tool == "get_settlements":
        yesterday = bool(re.search(r"\b(ontem|yesterday)\b", ctx.text, re.I))
        st = run("get_settlements", {"period": "yesterday" if yesterday else "upcoming"})
        if not st.get("ok"):
            return run.done("", status="error", reason="tool_error")
        d = st["data"]
        return run.done(_settlements_text(run, d, yesterday),
                        facts={"settlements_total": d["total"], "period": d["period"]})

    if tool == "get_transactions":
        txid = _TX_RE.search(ctx.text)
        args = {"transaction_id": txid.group(1).upper()} if txid else {}
        if not txid and (_DECLINE.search(ctx.text) or re.search(r"\b(erro|error)", ctx.text, re.I)):
            args = {"status": "recusada"}
        res = run("get_transactions", args)
        if not res.get("ok"):
            return run.done(t(ctx, f"Não encontrei a transação {args.get('transaction_id')} na sua conta.",
                              f"I couldn't find transaction {args.get('transaction_id')} on your account."),
                            facts={"transaction_not_found": args.get("transaction_id")})
        d = res["data"]
        declined = [x for x in d["transactions"] if x["status"] == "recusada"]
        en_reason = {"51": "insufficient funds on the cardholder's account", "05": "not authorized by the card issuer"}
        dec_pt = "; ".join(f"{x['transaction_id']} ({_brl(x['amount'])}) recusada com código {x['decline_code']}, "
                           f"{x['decline_reason']}" for x in declined)
        dec_en = "; ".join(f"{x['transaction_id']} ({_brl(x['amount'])}) declined with code {x['decline_code']}, "
                           f"{en_reason.get(x['decline_code'], x['decline_reason'])}" for x in declined)
        app_pt = (f"{d['approved_count']} aprovada(s), somando {_brl(d['approved_total'])}"
                  if d["approved_count"] else "")
        app_en = (f"{d['approved_count']} approved, adding up to {_brl(d['approved_total'])}"
                  if d["approved_count"] else "")
        if args.get("transaction_id") and not declined:
            x = d["transactions"][0]
            return run.done(t(ctx, f"A transação {x['transaction_id']} ({_brl(x['amount'])}, {_ddmm(x['date'])}) foi "
                                   "aprovada e pode ser contestada se você não a reconhecer.",
                              f"Transaction {x['transaction_id']} ({_brl(x['amount'])}, {_ddmm(x['date'])}) was "
                              "approved and can be disputed if you don't recognize it."),
                            facts={"transaction_id": x["transaction_id"], "status": x["status"]})
        pt_parts = [p for p in (app_pt and f"Transações recentes: {app_pt}",
                                dec_pt and f"Recusas: {dec_pt}") if p]
        en_parts = [p for p in (app_en and f"Recent transactions: {app_en}",
                                dec_en and f"Declines: {dec_en}") if p]
        if declined:
            pt_parts.append("As recusas vêm do banco do cartão, não de defeito da maquininha: peça outro cartão "
                            "ou outra forma de pagamento")
            en_parts.append("Declines come from the card's bank, not from a fault in your card machine: ask for "
                            "another card or payment method")
        if not pt_parts:
            pt_parts, en_parts = ["Não há transações recentes na sua conta"], ["There are no recent transactions"]
        return run.done(t(ctx, ". ".join(pt_parts) + ".", ". ".join(en_parts) + "."),
                        facts={"approved_total": d["approved_total"],
                               "declines": [{"transaction_id": x["transaction_id"], "code": x["decline_code"],
                                             "reason": x["decline_reason"]} for x in declined],
                               "query_hints": [f"código de recusa {x['decline_code']}" for x in declined]})

    # get_terminal_status
    tid = _TERM_RE.search(ctx.text)
    res = run("get_terminal_status", {"terminal_id": tid.group(1).upper()} if tid else {})
    if not res.get("ok"):
        return run.done(t(ctx, "Não encontrei essa maquininha na sua conta.",
                          "I couldn't find that card machine on your account."), facts={"terminal_not_found": True})
    terms = res["data"]["terminals"]
    bad = [x for x in terms if not x["online"] or x["signal"] != "ok" or x["blocked"]]
    if bad:
        x = bad[0]
        conn_pt = {"chip": "no chip", "wifi": "no Wi-Fi"}.get(x["connection"], x["connection"])
        conn_en = {"chip": "on the SIM chip", "wifi": "on Wi-Fi"}.get(x["connection"], x["connection"])
        others = [y for y in terms if y not in bad]
        ok_pt = f" A {others[0]['terminal_id']} ({others[0]['model']}) está online." if others else ""
        ok_en = f" {others[0]['terminal_id']} ({others[0]['model']}) is online." if others else ""
        return run.done(t(ctx, f"Sua maquininha {x['terminal_id']} ({x['model']}) está sem sinal {conn_pt} e "
                               f"offline há {x['last_contact_min']} minutos.{ok_pt}",
                          f"Your card machine {x['terminal_id']} ({x['model']}) has no signal {conn_en} and has "
                          f"been offline for {x['last_contact_min']} minutes.{ok_en}"),
                        facts={"terminal_id": x["terminal_id"], "model": x["model"], "connection": x["connection"],
                               "signal": x["signal"], "online": x["online"],
                               "query_hints": [f"{x['model']} sem sinal {x['connection']} não conecta"]})
    names = ", ".join(f"{x['terminal_id']} ({x['model']})" for x in terms)
    return run.done(t(ctx, f"Suas maquininhas estão online e com sinal: {names}.",
                      f"Your card machines are online with signal: {names}."),
                    facts={"terminals_ok": [x["terminal_id"] for x in terms]})
