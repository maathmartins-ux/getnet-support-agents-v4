"""Pré-voo: transforma o kit no ponto de partida do desafio. Roda UMA vez, na sessão principal, logo após o
commit `chore: scaffold` e antes da SPEC.

Uso:
  python scripts/preflight.py            # cria o domínio do desafio e limpa os exemplos do produto
  python scripts/preflight.py --dry-run  # só mostra o que faria

O que faz (cada item é uma lição da Getnet v3, 31/10/2026):
1. Cria `src/app/domain/challenge/` como CÓPIA do domínio de exemplo, em 3 arquivos com dono único:
   - `flow.py`  (frente R): taxonomia, planos, FALLBACK, regras do router offline;
   - `tools.py` (frente T): ferramentas do CRM e caminho offline do agente records;
   - `__init__.py` (sessão principal): MIN_SCORE calibrado e a junção dos dois.
   Na v3 o domínio editou router.py/supervisor.py/tools.py/agents.py e 59 testes do kit quebraram. Agora o
   motor é congelado (OWNERSHIP.json) e lê o domínio ativo.
2. Copia os prompts do kit para `src/app/domain/challenge_prompts/` (o desafio edita a cópia).
3. Tira a loja de exemplo do PRODUTO: data/sample.jsonl, data/records.json, data/intent_examples.jsonl, o
   glossário da loja e os casos/retrieval/calibração de exemplo em evals/ (os originais ficam em
   tests/fixtures/kit_data, que é o que os testes do kit usam). Na v3, 3 textos da loja ficaram no índice de
   busca e 9 perguntas da loja entraram no Recall@3.
4. Roda a suíte do kit: tem de seguir verde com o domínio do desafio no lugar.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = ROOT / "src" / "app" / "domain"
EXAMPLE = DOMAIN / "example.py"
TARGET = DOMAIN / "challenge"
PROMPTS = ROOT / "src" / "app" / "prompts"
TARGET_PROMPTS = DOMAIN / "challenge_prompts"

# Seções do example.py (marcadas por "# --- <nome>") → arquivo do pacote do desafio.
SECTION_FILE = {
    "taxonomia": "flow.py",
    "regras do router": "flow.py",
    "portão": "__init__.py",
    "prompts": "__init__.py",
    "ferramentas": "tools.py",
    "caminho offline": "tools.py",
}
HEADERS = {
    "flow.py": '"""Domínio do desafio — FLUXO (frente R): taxonomia, planos, FALLBACK, regras do router.\n\n'
               'Contrato lido pelo motor: PLANS, FALLBACK, ALLOWED, DEPENDS, NO_ESCALATION, DEFAULT_INTENT,\n'
               'MULTI_PARTS, states_subject(), clause_intent(), combine().\nTeste: tests/test_domain_router.py.\n"""\n',
    "tools.py": '"""Domínio do desafio — FERRAMENTAS (frente T): registro TOOLS e caminho offline do records.\n\n'
                'Contrato lido pelo motor: TOOLS, ELIGIBLE_STATUS, records(), records_offline(). Teste em\n'
                'tests/test_tools.py. Nenhuma tool da loja de exemplo pode sobrar aqui (check.py reprova).\n"""\n',
    "__init__.py": '"""Domínio do desafio (sessão principal): junta flow + tools e guarda o limiar calibrado.\n\n'
                   'MIN_SCORE sai de `python -m evals.run_evals --calibrate-gate` NO CORPUS DO DESAFIO; troque aqui,\n'
                   'nunca no motor nem no domínio de exemplo (v3: trocar o padrão do kit quebrou 14 testes).\n"""\n',
}
IMPORTS = {
    "flow.py": "from __future__ import annotations\n\nimport re\n",
    "tools.py": "from __future__ import annotations\n\nimport json\nimport re\nfrom functools import lru_cache\n\n"
                "from app.config import data_dir\n",
    "__init__.py": "from __future__ import annotations\n\nfrom pathlib import Path\n",
}
SOURCES = ('{\n  "_doc": "Páginas do site oficial da empresa para `python -m app.ingest` (frente K). '
           'allowed_hosts = só o domínio do país do enunciado.",\n  "allowed_hosts": [],\n  "groups": {\n'
           '    "produtos": {"prefix": "prod", "pages": {}},\n    "suporte": {"prefix": "sup", "pages": {}}\n  }\n}\n')
PROMPTS_LINE = 'PROMPTS_DIR = Path(__file__).resolve().parent.parent / "challenge_prompts"'
# Exemplos que saem do produto (o original continua em tests/fixtures/kit_data).
EMPTIED = ["data/sample.jsonl", "data/records.json", "data/intent_examples.jsonl",
           "evals/cases.jsonl", "evals/retrieval.jsonl", "evals/judge_calibration.jsonl"]
GLOSSARY = ('{\n  "_doc": "Glossário pt -> en do DESAFIO (agents.expand_query): chave = termo do corpus em português; '
            'valor = como o cliente pergunta em inglês. Preenchido pela frente K."\n}\n')


def split_example(text: str) -> dict[str, str]:
    """Corta o example.py nas seções '# --- nome' e devolve o corpo de cada arquivo do pacote."""
    parts = re.split(r"^(# --- [^\n]*)$", text, flags=re.MULTILINE)
    files: dict[str, list[str]] = {name: [] for name in HEADERS}
    for marker, body in zip(parts[1::2], parts[2::2], strict=True):
        name = marker[len("# --- "):].split(" (")[0].split(" -")[0].strip()
        target = next((f for key, f in SECTION_FILE.items() if name.startswith(key)), None)
        if target is None:
            sys.exit(f"ERRO: seção sem destino no pré-voo: {marker!r}")
        files[target].append(marker + body)
    return {name: "".join(chunks) for name, chunks in files.items()}


def build(dry: bool) -> list[str]:
    done = []
    if TARGET.exists() or (DOMAIN / "challenge.py").exists():
        sys.exit("ERRO: o domínio do desafio já existe; o pré-voo roda uma vez só.")
    bodies = split_example(EXAMPLE.read_text(encoding="utf-8"))
    contents = {
        "flow.py": HEADERS["flow.py"] + IMPORTS["flow.py"] + "\n" + bodies["flow.py"],
        "tools.py": HEADERS["tools.py"] + IMPORTS["tools.py"] + "\n" + bodies["tools.py"],
        "__init__.py": HEADERS["__init__.py"] + IMPORTS["__init__.py"] + "\nfrom .flow import *  # noqa: F403\n"
                       "from .tools import *  # noqa: F403\n\n" + bodies["__init__.py"].replace(
                           "PROMPTS_DIR = None", PROMPTS_LINE),
    }
    for name, body in contents.items():
        done.append(f"criar src/app/domain/challenge/{name}")
        if not dry:
            TARGET.mkdir(exist_ok=True)
            (TARGET / name).write_text(body, encoding="utf-8")
    done.append("copiar src/app/prompts/*.md → src/app/domain/challenge_prompts/")
    if not dry:
        shutil.copytree(PROMPTS, TARGET_PROMPTS)
    for rel in EMPTIED:
        done.append(f"esvaziar {rel} (exemplo da loja; original em tests/fixtures/kit_data)")
        if not dry:
            path = ROOT / rel
            if rel.endswith(".json"):
                path.unlink(missing_ok=True)
            else:
                path.write_text("", encoding="utf-8")
    done.append("trocar data/glossary.json pelo esqueleto do desafio")
    done.append("criar data/sources.json (frente K: domínios e páginas do site da empresa)")
    if not dry:
        (ROOT / "data" / "glossary.json").write_text(GLOSSARY, encoding="utf-8")
        (ROOT / "data" / "sources.json").write_text(SOURCES, encoding="utf-8")
    return done


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    for line in build(args.dry_run):
        print(("[simulação] " if args.dry_run else "") + line)
    if args.dry_run:
        return 0
    run([sys.executable, "-m", "ruff", "check", "--fix", "-q", str(TARGET)])  # imports que o corte não usa
    lint = run([sys.executable, "-m", "ruff", "check", "."])
    tests = run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
    probe = run([sys.executable, "-c", "import sys; sys.path.insert(0, 'src'); from app import domain; "
                                       "print(domain.name(), sorted(domain.active().PLANS))"])
    print(f"\nruff: {'OK' if lint.returncode == 0 else 'FALHOU'}\n{lint.stdout[-1500:]}")
    print(f"suíte do kit com o domínio do desafio no lugar: {tests.stdout.strip().splitlines()[-1:]}")
    print(f"domínio ativo fora dos testes: {probe.stdout.strip() or probe.stderr.strip()[-300:]}")
    ok = lint.returncode == 0 and tests.returncode == 0 and probe.stdout.startswith("challenge")
    print("\nPRÉ-VOO " + ("OK: commite com `git add -A && git commit -m 'preflight: domínio do desafio'`."
                          if ok else "FALHOU: não siga para a SPEC antes de corrigir."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
