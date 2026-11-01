"""Ingestão (motor do kit): extração literal, tamanho dos trechos, lista de domínios e gravação, sem rede."""

import hashlib
import json

import pytest
from conftest import KIT_DATA

from app.ingest import MAX_CHARS, MIN_CHARS, build, chunk, extract, fetch, is_allowed, load_sources, records

HOSTS = {"loja.example.com"}


def test_only_listed_hosts():
    assert is_allowed("https://loja.example.com/entrega", HOSTS)
    assert not is_allowed("https://loja.example.com.evil.example/", HOSTS)
    with pytest.raises(ValueError):
        fetch("https://outro-pais.example.com/", HOSTS)


def test_sources_file_needs_allowed_hosts(tmp_path):
    cfg = load_sources(KIT_DATA / "sources.json")
    assert cfg["allowed_hosts"] and cfg["groups"]
    bad = tmp_path / "s.json"
    bad.write_text(json.dumps({"groups": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_sources(bad)


def test_extract_drops_chrome_and_scripts():
    html = "<html><title>T</title><nav>Menu</nav><script>x=1</script><p>Texto <b>visível</b></p><footer>F</footer>"
    assert extract(html) == ("T", ["Texto visível"])


def test_chunk_respects_bounds_and_is_literal():
    lines = [f"Frase número {i} sobre o prazo de entrega da loja." for i in range(60)]
    joined = " ".join(lines)
    chunks = chunk(lines)
    assert chunks and all(MIN_CHARS <= len(c) <= MAX_CHARS and c in joined for c in chunks)


def test_chunk_splits_long_line_by_sentence():
    long_line = " ".join(f"Sentença {i} do texto longo." for i in range(80))
    assert all(MIN_CHARS <= len(c) <= MAX_CHARS and c in long_line for c in chunk([long_line]))


def test_records_shape():
    (r,) = records("pix", "https://loja.example.com/pix", "Pix", ["a" * 300], "2026-10-31T17:00:00+00:00", "prod")
    assert r["id"] == "prod-pix-01"
    assert r["sha256"] == hashlib.sha256(("a" * 300).encode()).hexdigest()
    assert set(r) == {"id", "title", "url", "text", "fetched_at", "sha256"}


def test_build_drops_repeated_site_chrome_and_writes_jsonl(tmp_path):
    menu = "Menu principal da loja com todos os links"
    pages = {f"p{i}": f"https://loja.example.com/p{i}" for i in range(4)}

    def fake_fetch(url, hosts):
        body = " ".join(f"Conteúdo próprio da página {url} frase {n}." for n in range(40))
        return f"<title>{url}</title><p>{menu}</p><p>{body}</p>"

    out = build(pages, tmp_path, HOSTS, "ajuda", fetcher=fake_fetch)
    rows = [json.loads(line) for f in sorted(tmp_path.glob("*.jsonl")) for line in f.read_text("utf-8").splitlines()]
    assert sum(len(v) for v in out.values()) == len(rows) > 0
    assert all(menu not in r["text"] and r["id"].startswith("ajuda-") for r in rows)
