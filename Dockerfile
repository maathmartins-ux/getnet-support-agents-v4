# EXTRAS vazio = imagem padrão (BM25). EXTRAS=hybrid = busca híbrida com o modelo de embeddings embutido no
# build: o container não baixa nada na subida e o primeiro request não paga download. Decida pelo eval
# (`run_evals --compare-retrievers`). Build: docker build --build-arg EXTRAS=hybrid -t app .
#
# Camadas na ordem de mudança: lockfile e modelo (raros) → dados → código (a cada commit). O app NÃO entra
# na venv: vai como pasta no PYTHONPATH, então mudar código não recria a venv. Medido em 31/10/2026, mudança
# só no src/: híbrido 451 s → 101 s (venv recopiada) → segundos (esta versão; ver check.py).
ARG EXTRAS=""
# Base fixada por digest (lido em 31/10/2026): build reprodutível; o Dependabot (.github/dependabot.yml) propõe a troca.
ARG EMBED_MODEL="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Estágio 1: só dependências exatas do lockfile (e o modelo, no híbrido), num venv isolado.
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 AS build
ARG EXTRAS
ARG EMBED_MODEL
COPY requirements.lock requirements-hybrid.lock /tmp/
# Sessão da NUVEM (proxy com certificado próprio): check.py passa o certificado como SEGREDO do build (não fica em
# nenhuma camada) e o proxy por build-arg. Fora da nuvem o segredo não existe e nada muda.
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca REQUESTS_CA_BUNDLE=/run/secrets/proxy_ca; fi \
 && python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements${EXTRAS:+-$EXTRAS}.lock \
 && mkdir -p /opt/models \
 && if [ "$EXTRAS" = "hybrid" ]; then \
      FASTEMBED_CACHE_PATH=/opt/models /opt/venv/bin/python -c \
        "import sys; from fastembed import TextEmbedding; TextEmbedding(sys.argv[1])" "$EMBED_MODEL"; \
    fi

# Estágio 2: runtime sem pytest/ruff, sem compilador, usuário não-root.
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
ARG EMBED_MODEL
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    DB_PATH=/app/var/app.db \
    FASTEMBED_CACHE_PATH=/opt/models \
    HF_HUB_OFFLINE=1 \
    EMBED_MODEL=$EMBED_MODEL
WORKDIR /app
RUN useradd --uid 10001 --create-home appuser && mkdir -p /app/var && chown appuser /app/var
# modelo é do appuser (a lib grava índice do cache) e offline: o container não busca nada na internet
COPY --from=build --chown=10001:10001 /opt/models /opt/models
COPY --from=build /opt/venv /opt/venv
COPY data ./data
COPY src ./src
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
