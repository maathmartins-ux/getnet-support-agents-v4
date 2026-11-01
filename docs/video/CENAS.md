# Comandos por cena (apoio à gravação de docs/VIDEO_SCRIPT.md)

```bash
# cena 3 — payload literal (AUTH_MODE=demo)
AUTH_MODE=demo docker compose up -d --build
for m in "What's the difference between the Get Clássica and the Get Smart?" \
         "When will the money from yesterday's sales be deposited?" \
         "My card machine won't connect to the internet, what should I do?" \
         "What's the euro exchange rate today?"; do
  jq -n --arg m "$m" '{message:$m,user_id:"cliente1988"}' |
    curl -s -X POST localhost:8000/chat -H "Content-Type: application/json" -d @- | jq '{answer,intent,route,sources}'
done

# cena 5 — ferramenta com confirmação (token verificado)
TOKEN=$(docker compose exec -T api python -m app.auth cliente1988)
curl -s -X POST localhost:8000/chat -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"message":"Quero antecipar meus recebíveis"}' | jq '.pending_action'

# cena 6 — operador
open http://localhost:8000/operator

# cena 9 — produção
python scripts/check.py --docker --scale
curl -s localhost:8000/metrics | head -40
```
