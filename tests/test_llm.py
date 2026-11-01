"""Camada LLM: forma do request (effort explícito, sem sampling, sem tool_choice) e erros classificados."""
import anthropic
import httpx2
import pytest
from fakes import fake_llm, resp, text

from app.llm import LLMError, OfflineLLM, get_llm
from app.observability import METRICS

REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(cls, code):
    return cls("falhou", response=httpx2.Response(code, request=REQ), body=None)


def test_offline_is_default_and_never_calls_out():
    assert isinstance(get_llm("router"), OfflineLLM)
    with pytest.raises(LLMError):
        get_llm("agent").complete("s", [])


def test_complete_with_schema_uses_output_config():
    llm, msgs = fake_llm([resp("end_turn", text('{"ok": true}'))], model="claude-haiku-5-5", effort="low")
    assert llm.complete("sys", [{"role": "user", "content": "oi"}], schema={"type": "object"}).json() == {"ok": True}
    sent = msgs.sent[0]
    assert sent["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert not {"temperature", "top_p", "top_k", "tool_choice"} & sent.keys()


@pytest.mark.parametrize("exc,kind", [
    (status_error(anthropic.RateLimitError, 429), "rate_limited"),
    (status_error(anthropic.AuthenticationError, 401), "auth"),
    (status_error(anthropic.InternalServerError, 500), "api_status"),
    (anthropic.APIConnectionError(request=REQ), "connection"),
])
def test_sdk_errors_become_classified_llm_errors(exc, kind):
    llm, _ = fake_llm([exc])
    before = METRICS.get("llm_errors_total", model="claude-sonnet-5-5", kind=kind)
    with pytest.raises(LLMError) as err:
        llm.complete("s", [{"role": "user", "content": "x"}])
    assert err.value.kind == kind
    assert METRICS.get("llm_errors_total", model="claude-sonnet-5-5", kind=kind) == before + 1


def test_refusal_and_truncated_json_are_errors():
    llm, _ = fake_llm([resp("refusal", text("")), resp("max_tokens", text('{"intent": "fa'))])
    with pytest.raises(LLMError) as refusal:
        llm.complete("s", [{"role": "user", "content": "x"}])
    with pytest.raises(LLMError) as truncated:
        llm.complete("s", [{"role": "user", "content": "x"}], schema={"type": "object"})
    assert (refusal.value.kind, truncated.value.kind) == ("refusal", "max_tokens")


def test_warmup_builds_one_client_per_role_only_when_live(monkeypatch):
    # smoke de 31/10/2026: `import anthropic` levava ~7 s e caía dentro do 1º request de cada processo
    from app import llm as llm_mod
    from app.config import ROLES

    built = []
    monkeypatch.setattr(llm_mod, "_anthropic", lambda model, effort: built.append((model, effort)))
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    assert llm_mod.warmup() == 0 and built == []
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert llm_mod.warmup() == len({v for v in ROLES.values()}) == len(set(built))


def test_warmup_never_breaks_startup(monkeypatch):
    from app import llm as llm_mod

    def boom(model, effort):
        raise RuntimeError("sem rede")

    monkeypatch.setattr(llm_mod, "_anthropic", boom)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert llm_mod.warmup() == 0


def test_app_key_and_public_api_in_the_cloud(monkeypatch):
    # nuvem: ANTHROPIC_API_KEY é do Claude Code e ANTHROPIC_BASE_URL aponta para a conexão interna dele
    from app.config import PUBLIC_API, llm_api_key, llm_base_url

    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://conexao-interna-do-claude-code")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("APP_ANTHROPIC_API_KEY", "sk-ant-api-teste")
    assert llm_api_key() == "sk-ant-api-teste" and llm_base_url() == PUBLIC_API
    monkeypatch.setenv("APP_ANTHROPIC_BASE_URL", "http://gateway-do-app")
    assert llm_base_url() == "http://gateway-do-app"


def test_local_keeps_the_usual_names(monkeypatch):
    from app.config import llm_api_key, llm_base_url

    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    monkeypatch.delenv("APP_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("APP_ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-local")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://proxy-local")
    assert llm_api_key() == "sk-ant-api-local" and llm_base_url() == "http://proxy-local"


def test_key_pasted_with_spaces_or_quotes_is_cleaned(monkeypatch):
    from app.config import llm_api_key

    monkeypatch.setenv("APP_ANTHROPIC_API_KEY", ' "sk-ant-api03-abc"\n')
    assert llm_api_key() == "sk-ant-api03-abc"
