# ADR 0001 — AUTH_MODE demo × required

Status: aceito no kit v3 · revisar no commit da spec de cada desafio

**Contexto.** Enunciados de desafio costumam mandar `user_id` no corpo (`{"message", "user_id"}`).
Confiar no corpo é falsificação trivial de identidade; ignorar o corpo quebra o payload literal
que o avaliador vai colar.

**Decisão.** Dois modos, escolhidos por `AUTH_MODE`:
- `required` (padrão do código): identidade só do Bearer token assinado; o `user_id` do corpo é ignorado.
- `demo`: um Bearer válido sempre vence; sem token, aceita o `user_id` do corpo, marca
  `identity_verified=false` na resposta e no log e aplica o rate limit **por IP** (trocar o
  `user_id` a cada request não zera o limite). Token presente e inválido é 401 nos dois modos.
O desafio que exige o payload literal liga `demo` no compose e declara isso no README.

**Consequências.** O avaliador cola o payload do enunciado e funciona; ninguém confunde demo com
segurança, porque a resposta diz `identity_verified=false`. Os dois modos têm teste
(`tests/test_auth_modes.py`), incluindo o abuso de girar `user_id`. Em produção: `required` +
JWT/OIDC no lugar do HMAC (a interface `verify_token -> user_id` é a mesma) e `APP_ENV=production`,
que recusa subir com segredo padrão.
