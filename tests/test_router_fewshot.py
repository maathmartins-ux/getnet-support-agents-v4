"""Item 4 — few-shot dinâmico no router com LLM: os 8 exemplos rotulados mais parecidos, o mais parecido por último.

O enum de intents continua estrito no JSON schema: o exemplo ensina, o schema garante o contrato.
"""
import json

from fakes import fake_llm, resp, text

from app import router
from app.router import FEW_SHOT_K, get_knn, route
from app.supervisor import PLANS


def use(monkeypatch, llm):
    monkeypatch.setattr(router, "get_llm", lambda role="agent": llm)


def decision(intent="records"):
    return resp("end_turn", text(json.dumps({"intent": intent, "plan": PLANS[intent], "language": "pt",
                                             "confidence": 0.9})))


def sent_prompt(msgs):
    return msgs.sent[0]["messages"][0]["content"]


def test_prompt_has_nearest_examples_most_similar_last(monkeypatch):
    llm, msgs = fake_llm([decision()], model="claude-haiku-5-5", effort="low")
    use(monkeypatch, llm)
    assert route("minhas compras já chegaram?", []).intent == "records"
    prompt = sent_prompt(msgs)
    knn = get_knn(clause_level=False)
    expected = [knn.examples[i] for _, i in knn.neighbors("minhas compras já chegaram?", FEW_SHOT_K)][::-1]
    shown = [line for line in prompt.splitlines() if line.startswith("- \"")]
    assert len(shown) == FEW_SHOT_K == 8
    assert shown == [f"- {json.dumps(t, ensure_ascii=False)} -> {i}" for t, i in expected]
    # o mais parecido fica por último, colado na mensagem
    assert "minha compra" in shown[-1] or "compras" in shown[-1]
    assert prompt.rstrip().endswith("Mensagem: minhas compras já chegaram?")


def test_schema_enum_stays_strict(monkeypatch):
    llm, msgs = fake_llm([decision()], model="claude-haiku-5-5", effort="low")
    use(monkeypatch, llm)
    route("minhas compras já chegaram?", [])
    schema = msgs.sent[0]["output_config"]["format"]["schema"]
    assert schema == router.SCHEMA
    assert schema["properties"]["intent"]["enum"] == sorted(PLANS)


def test_no_examples_no_few_shot_block(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    llm, msgs = fake_llm([decision("faq")], model="claude-haiku-5-5", effort="low")
    use(monkeypatch, llm)
    route("qual o prazo?", [])
    assert "Exemplos rotulados" not in sent_prompt(msgs)


def test_prompt_carries_conversation_intents_beyond_recent_window(monkeypatch):
    # item 2 no caminho com LLM: o assunto do 1º turno chega mesmo fora das 4 falas recentes
    history = [{"role": "user", "content": "Onde está meu pedido 1002?", "intent": "records"}]
    history += [{"role": r, "content": "Ok, entendi.", "intent": "faq"}
                for _ in range(6) for r in ("user", "assistant")]
    llm, msgs = fake_llm([decision()], model="claude-haiku-5-5", effort="low")
    use(monkeypatch, llm)
    route("E ele já saiu?", history)
    prompt = sent_prompt(msgs)
    assert "Assunto da conversa (última fala com assunto): records" in prompt
