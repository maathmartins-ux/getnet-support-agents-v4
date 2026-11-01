"""ops/alerts.yml: regras válidas, com histerese e ação, e só com métricas que o app de fato emite."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def emitted_metrics() -> set[str]:
    code = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src" / "app").glob("*.py"))
    names = set(re.findall(r'METRICS\.(?:inc|add)\("([a-z_]+)"', code))
    return names | {"span_latency_ms_sum", "span_latency_ms_count"}  # render() de observability.py


def test_alert_rules_are_valid_and_actionable():
    rules = yaml.safe_load((ROOT / "ops" / "alerts.yml").read_text(encoding="utf-8"))["groups"][0]["rules"]
    assert len(rules) >= 5
    known = emitted_metrics()
    for rule in rules:
        assert rule["alert"] and rule["expr"] and rule["for"], rule
        assert rule["annotations"]["action"], f"{rule['alert']} sem ação: alerta sem ação vira ruído"
        used = set(re.findall(r"\b([a-z_]+_(?:total|sum|count))\b", rule["expr"]))
        assert used and used <= known, f"{rule['alert']} usa métrica que o app não emite: {used - known}"
