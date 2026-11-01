"""Ingestão do corpus (motor do kit): baixa as páginas listadas em data/sources.json e grava trechos LITERAIS.

Uso: python -m app.ingest [--sources data/sources.json] [--group produtos]

Cada linha: {id, title, url, text, fetched_at, sha256}. `text` é recorte literal do HTML (só texto visível, sem
script/menu/rodapé), em trechos de 300–800 caracteres. Linha repetida em metade das páginas é chrome do site
(menu, banner de cookie) e sai. Só baixa de `allowed_hosts` (o domínio do país do enunciado).

Veio da Getnet v3 (fork A1, 31/10/2026), onde custou ~30 min de relógio; no kit, a frente K só preenche
data/sources.json. Na nuvem, o domínio precisa estar liberado na rede do ambiente (scripts/cloud_probe.py).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

MIN_CHARS, MAX_CHARS = 300, 800

SKIP_TAGS = {"script", "style", "noscript", "svg", "header", "footer", "nav", "template", "iframe", "form"}
BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "li",
    "ul",
    "ol",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "br",
    "tr",
    "td",
    "th",
    "table",
    "dt",
    "dd",
    "button",
    "a",
    "span_block",
    "summary",
    "details",
    "main",
    "aside",
}


def load_sources(path: Path) -> dict:
    """data/sources.json: {"allowed_hosts": [...], "groups": {"<pasta>": {"prefix": "...", "pages": {slug: url}}}}."""
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not cfg.get("allowed_hosts"):
        raise ValueError(f"{path}: allowed_hosts vazio (o domínio do país do enunciado, nada mais)")
    return cfg


def is_allowed(url: str, allowed_hosts: set[str] | list[str]) -> bool:
    return urlparse(url).hostname in set(allowed_hosts)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._buf: list[str] = []
        self._skip = 0
        self.title = ""
        self._in_title = False

    def _flush(self) -> None:
        line = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        if line:
            self.lines.append(line)
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def extract(html: str) -> tuple[str, list[str]]:
    p = _TextExtractor()
    p.feed(html)
    p.close()
    title = re.sub(r"\s+", " ", p.title).strip()
    return title, p.lines


def chunk(lines: list[str], lo: int = MIN_CHARS, hi: int = MAX_CHARS) -> list[str]:
    """Agrupa linhas consecutivas em trechos de lo..hi caracteres; linha maior que hi é cortada em frase."""
    pieces: list[str] = []
    for line in lines:
        if len(line) <= hi:
            pieces.append(line)
            continue
        cur = ""
        for sent in re.split(r"(?<=[.!?])\s+", line):
            if cur and len(cur) + 1 + len(sent) > hi:
                pieces.append(cur)
                cur = sent
            else:
                cur = f"{cur} {sent}".strip()
        if cur:
            pieces.append(cur[:hi])
    out: list[str] = []
    cur = ""
    for piece in pieces:
        if cur and len(cur) + 1 + len(piece) > hi:
            if len(cur) >= lo:
                out.append(cur)
                cur = piece
                continue
            cur = ""  # sobra curta que não cabe com a próxima peça: descarta (abaixo do mínimo)
        cur = f"{cur} {piece}".strip()
        if len(cur) >= lo and piece.endswith((".", "!", "?", ":")):
            out.append(cur)
            cur = ""
    if len(cur) >= lo:
        out.append(cur)
    elif cur and out and len(out[-1]) + 1 + len(cur) <= hi:
        out[-1] = f"{out[-1]} {cur}"
    return [c for c in out if lo <= len(c) <= hi]


def records(slug: str, url: str, title: str, chunks: list[str], fetched_at: str, prefix: str = "doc") -> list[dict]:
    return [
        {
            "id": f"{prefix}-{slug}-{i:02d}",
            "title": title,
            "url": url,
            "text": text,
            "fetched_at": fetched_at,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        for i, text in enumerate(chunks, 1)
    ]


def fetch(url: str, allowed_hosts: set[str] | list[str]) -> str:
    if not is_allowed(url, allowed_hosts):
        raise ValueError(f"domínio fora da lista de sources.json: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (support-agents ingest)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def build(pages: dict[str, str], out_dir: Path, allowed_hosts, prefix: str = "doc",
          fetcher=fetch) -> dict[str, list[dict]]:
    """Baixa, extrai e grava um grupo. Linha repetida em metade das páginas (ou 3+) é chrome do site e sai."""
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    parsed = {slug: (url, *extract(fetcher(url, allowed_hosts))) for slug, url in pages.items()}
    freq = Counter(line for _, _, lines in parsed.values() for line in set(lines))
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for slug, (url, title, lines) in parsed.items():
        body = [ln for ln in lines if freq[ln] < max(3, len(parsed) // 2) and len(ln) >= 15]
        recs = []
        for r in records(slug, url, title, chunk(body), fetched_at, prefix):
            if r["sha256"] not in seen:
                seen.add(r["sha256"])
                recs.append(r)
        result[slug] = recs
        with (out_dir / f"{slug}.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="data/sources.json")
    ap.add_argument("--group", help="só um grupo de sources.json (padrão: todos)")
    args = ap.parse_args()
    cfg = load_sources(Path(args.sources))
    total = 0
    for name, group in cfg["groups"].items():
        if args.group and name != args.group:
            continue
        result = build(group["pages"], Path("data/knowledge") / name, cfg["allowed_hosts"], group.get("prefix", name))
        for slug, recs in result.items():
            print(f"{name}/{slug}: {len(recs)} trechos")
        total += sum(len(r) for r in result.values())
    print(f"total: {total}")


if __name__ == "__main__":
    main()
