"""Balanceador round-robin mínimo (só biblioteca padrão) para o teste de réplicas.

Roda na PRÓPRIA imagem do app (sem baixar nginx: o Docker Hub devolveu 429 "Too Many Requests" na nuvem em
01/11/2026). Reresolve o nome do serviço a cada requisição (réplica nova entra, réplica que cai sai) e, se uma
réplica não responde, tenta a próxima. Não é para produção: em produção, o balanceador da plataforma.
"""
import http.client
import itertools
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BACKEND, PORT = os.getenv("LB_BACKEND", "api"), int(os.getenv("LB_BACKEND_PORT", "8000"))
_counter = itertools.count()
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization"}


def backends() -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(BACKEND, PORT, socket.AF_INET)})
    except OSError:
        return []


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _forward(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        ips = backends()
        start = next(_counter)
        for k in range(len(ips)):
            ip = ips[(start + k) % len(ips)]
            try:
                conn = http.client.HTTPConnection(ip, PORT, timeout=30)
                headers = {h: v for h, v in self.headers.items() if h.lower() not in HOP}
                conn.request(self.command, self.path, body=body or None, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
            except OSError:
                continue  # réplica fora: tenta a próxima
            self.send_response(resp.status)
            for h, v in resp.getheaders():
                if h.lower() not in HOP and h.lower() != "content-length":
                    self.send_header(h, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(502, "nenhuma réplica respondeu")

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _forward

    def log_message(self, *args) -> None:  # sem log por requisição
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Proxy).serve_forever()
