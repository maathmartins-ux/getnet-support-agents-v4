"""Item 5 — contaminação do held-out: regra do roteador nem exemplo do k-NN podem copiar frase do teste cego.

Origem: v3 (31/10/2026). A frase do cego "uma pessoa de verdade" foi copiada para a regra _HUMAN e o número
"cego" virou falso: a regra acertava porque tinha visto a prova. Teste de DOMÍNIO: roda sobre os arquivos
REAIS do projeto (src/app/router.py e evals/heldout_blind.jsonl); no kit, sem held-out, pula com o motivo.
"""
import json
import re
import unicodedata
from pathlib import Path

import pytest

from app import router
from app.config import ROOT, data_dir


def normalize(text: str) -> str:
    """Própria do teste (não importa do router): o detector não pode depender do código que ele audita."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", plain))


HELDOUT = ROOT / "evals" / "heldout_blind.jsonl"
MIN_WORDS = 3  # 2 palavras ("pessoa real", "e aproveita") é vocabulário; 3 já é frase copiada
SHINGLE = 5  # exemplo do k-NN que repete 5 palavras seguidas de uma pergunta do cego é cópia


def rule_literals(patterns: list[str]) -> set[str]:
    """Trechos literais (texto puro, sem metacaractere) de cada regex, normalizados.

    Classe de caractere vira a primeira letra ("algu[eé]m" -> "alguem"); \\s vira espaço; o resto
    (grupos, alternativas, \\w*, quantificadores) corta o trecho.
    """
    out = set()
    for pat in patterns:
        p = re.sub(r"\[\^?([^\]])[^\]]*\]", r"\1", pat)  # [eé] -> e
        p = re.sub(r"\\s[+*]?", " ", p)
        p = re.sub(r"\(\?[:=!]|\(\?<[=!]|\\[a-zA-Z](\{[^}]*\})?[*+?]?|\{[^}]*\}", "|", p)
        for run in re.split(r"[()|?*+.^$\\]", p):
            words = normalize(run).split()
            if len(words) >= MIN_WORDS:
                out.add(" ".join(words))
    return out


def router_patterns(module=router) -> list[str]:
    return [v.pattern for v in vars(module).values() if isinstance(v, re.Pattern)]


def load_heldout(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def contaminated_rules(patterns: list[str], heldout: list[dict]) -> list[tuple[str, str]]:
    hits = []
    for lit in sorted(rule_literals(patterns)):
        for case in heldout:
            if f" {lit} " in f" {normalize(case['message'])} ":
                hits.append((lit, case.get("id", "?")))
    return hits


def shingles(text: str, n: int = SHINGLE) -> set[str]:
    w = normalize(text).split()
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)} or ({" ".join(w)} if w else set())


def contaminated_examples(examples: list[str], heldout: list[dict]) -> list[tuple[str, str]]:
    blind = [(c.get("id", "?"), shingles(c["message"])) for c in heldout]
    return [(ex, cid) for ex in examples for cid, sh in blind if shingles(ex) & sh]


# --- o detector (roda sempre, inclusive no kit) --------------------------------------------------------

def test_detector_catches_v3_leak(tmp_path):
    heldout = [{"id": "heldout-16", "message": "Quero falar com uma pessoa de verdade, cansei de robô."},
               {"id": "heldout-99", "message": "Quero falar com alguém agora"}]
    patterns = [r"\b(atendente|pessoa real|falar com algu[eé]m|uma pessoa de verdade)\b"]
    assert contaminated_rules(patterns, heldout) == [("falar com alguem", "heldout-99"),
                                                     ("uma pessoa de verdade", "heldout-16")]


def test_detector_ignores_short_vocabulary_and_regex_syntax():
    heldout = [{"id": "h1", "message": "quanto vendi ontem? e aproveita: pessoa real"}]
    patterns = [r"(vendas de (ontem|hoje)|vendi\b|antecip\w* .{0,30}\b(meus|my) receb|\d{4}|pessoa real)",
                r",?\s+e\s+(?:aproveita|tamb[eé]m)\b"]
    assert contaminated_rules(patterns, heldout) == []


def test_detector_catches_copied_example():
    heldout = [{"id": "h1", "message": "List my approved sales so I can match them with my notebook, please."}]
    assert contaminated_examples(["list my approved sales so I can check"], heldout) == [
        ("list my approved sales so I can check", "h1")]
    assert contaminated_examples(["show my approved transactions"], heldout) == []


# --- o teste de domínio (arquivos reais do projeto) -----------------------------------------------------

def test_router_rules_do_not_copy_heldout():
    if not HELDOUT.exists():
        pytest.skip(f"sem held-out em {HELDOUT.relative_to(ROOT)}: o domínio cria o cego no relógio")
    hits = contaminated_rules(router_patterns(), load_heldout(HELDOUT))
    assert not hits, f"regra do roteador copia o held-out (o número cego vira falso): {hits}"


def test_knn_examples_do_not_copy_heldout(domain_data):
    from app.jsonl import family, read_family

    examples_path = data_dir() / router.EXAMPLES_FILE
    if not HELDOUT.exists() or not family(examples_path):
        pytest.skip("sem held-out ou sem data/intent_examples.jsonl(.d): nada a comparar")
    examples = [r["text"] for r in read_family(examples_path)]  # base + intent_examples.d/*.jsonl
    hits = contaminated_examples(examples, load_heldout(HELDOUT))
    assert not hits, f"exemplo do k-NN copia o held-out (treino no cego): {hits}"
