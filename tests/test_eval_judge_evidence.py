"""Item 4: juiz cego para dados de ferramenta (Getnet v3, 31/10/2026).

O juiz só recebia os trechos do RAG: 46 de 47 respostas "não grounded" usavam dado do CRM ou da web. Cada
linha do eval grava a evidência que a API devolve (tools chamadas, fontes com título/URL e, quando o
contrato trouxer, o texto da evidência), e o juiz recebe isso como "Dados de ferramenta".
"""
import json

from evals import judge, run_evals
from evals.run_evals import run

CRM = '{"record_id": "1002", "status": "em separação", "item": "Livro B"}'


class EvidenceClient:
    """Resposta como a API devolveria com o contrato completo (tool_calls, sources e evidence)."""

    def send(self, message, user_id, conversation_id):
        return {"intent": "records", "conversation_id": "c", "answer": "Seu pedido 1002 está em separação.",
                "route": ["router", "records"], "replans": [], "usage": {"cost_usd": 0.0},
                "tool_calls": [{"name": "lookup_record", "args": {"record_id": "1002"}, "ok": True}],
                "sources": [{"id": "web-1", "title": "Previsão POA", "url": "https://tempo.example/poa"}],
                "evidence": [CRM]}


def test_live_rows_record_tool_calls_sources_and_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(run_evals, "RESULTS_DIR", tmp_path)
    row = run(split="official", live=True, client=EvidenceClient())["rows"][0]
    assert row["tool_calls"] == [{"name": "lookup_record", "args": {"record_id": "1002"}, "ok": True}]
    assert row["sources"] == [{"id": "web-1", "title": "Previsão POA", "url": "https://tempo.example/poa"}]
    assert row["evidence"] == [CRM]


def test_in_process_rows_record_real_tool_calls(tmp_path):
    report = run(out=tmp_path / "latest.json", split="official")
    row = next(r for r in report["rows"] if r["id"] == "official-03")
    assert [c["name"] for c in row["tool_calls"]] == ["lookup_record"]
    assert row["tool_calls"][0]["args"] == {"record_id": "1002"}


class CaptureJudge:
    model = "capture"

    def __init__(self):
        self.prompts = []

    def complete(self, system, messages, max_tokens=1024, schema=None):
        from app.llm import LLMReply

        self.prompts.append(messages[0]["content"])
        return LLMReply(json.dumps({"grounded": True, "helpful": True, "score": 5, "reason": "ok"}), self.model)


def test_judge_receives_tool_data_and_web_sources(tmp_path):
    live = tmp_path / "live_official.json"
    live.write_text(json.dumps({"rows": [{
        "id": "a", "split": "official", "message": "Onde está o 1002?", "answer": "Em separação.",
        "source_ids": ["web-1"], "sources": [{"id": "web-1", "title": "Previsão POA",
                                              "url": "https://tempo.example/poa"}],
        "tool_calls": [{"name": "lookup_record", "args": {"record_id": "1002"}, "ok": True}],
        "evidence": [CRM]}]}, ensure_ascii=False), encoding="utf-8")
    cal = tmp_path / "cal.jsonl"
    cal.write_text("", encoding="utf-8")
    llm = CaptureJudge()
    judge.run(llm, [live], calibration=cal)
    prompt = llm.prompts[-1]
    assert "Dados de ferramenta" in prompt and "em separação" in prompt and "lookup_record" in prompt
    assert "Previsão POA" in prompt and "https://tempo.example/poa" in prompt


def test_judge_declares_missing_tool_data_on_old_files(tmp_path):
    live = tmp_path / "live_official.json"
    live.write_text(json.dumps({"rows": [{"id": "a", "split": "official", "message": "Prazo?",
                                          "answer": "3 dias.", "source_ids": []}]}), encoding="utf-8")
    cal = tmp_path / "cal.jsonl"
    cal.write_text("", encoding="utf-8")
    llm = CaptureJudge()
    judge.run(llm, [live], calibration=cal)
    assert "Dados de ferramenta:\n(nenhum)" in llm.prompts[-1]
