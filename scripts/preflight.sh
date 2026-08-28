#!/usr/bin/env bash
# Проверка окружения перед работой. Запускать в начале каждой сессии.
# Ловит ровно те грабли, на которых проект уже спотыкался (см. docs/02-verified-facts.md).
set -uo pipefail

# Кто и куда: задаются локально, чтобы личная почта не уезжала в публичный репозиторий.
# Положить рядом .preflight.env (в .gitignore) или экспортировать перед запуском.
[ -f "$(dirname "$0")/.preflight.env" ] && . "$(dirname "$0")/.preflight.env"
PROJECT="${TRUTH_PROJECT:-merci-prod}"
EXPECTED_ADC="${TRUTH_ADC:-}"
ok=0; bad=0
say()  { printf '%-46s %s\n' "$1" "$2"; }
pass() { say "$1" "✅ $2"; ok=$((ok+1)); }
fail() { say "$1" "❌ $2"; bad=$((bad+1)); }
warn() { say "$1" "⚠️  $2"; }

echo "=== preflight «Я есть Правда» ==="

# 1. ADC — критично: определяет, ЧЕЙ кредит тратится (правило из ~/.claude/CLAUDE.md)
TOKEN=$(gcloud auth application-default print-access-token 2>/dev/null)
if [ -z "$TOKEN" ]; then
  fail "ADC" "нет токена → gcloud auth application-default login"
else
  ADC_EMAIL=$(curl -s "https://www.googleapis.com/oauth2/v3/tokeninfo?access_token=$TOKEN" \
              | python3 -c 'import sys,json; print(json.load(sys.stdin).get("email",""))' 2>/dev/null)
  if [ -z "$EXPECTED_ADC" ]; then
    warn "ADC identity" "$ADC_EMAIL (ожидаемый не задан — TRUTH_ADC в scripts/.preflight.env)"
  elif [ "$ADC_EMAIL" = "$EXPECTED_ADC" ]; then
    pass "ADC identity" "$ADC_EMAIL"
  else
    fail "ADC identity" "$ADC_EMAIL (ожидался $EXPECTED_ADC — иначе кредиты сгорают впустую)"
  fi
fi

# 2. Активный проект
ACTIVE=$(gcloud config get-value project 2>/dev/null)
[ "$ACTIVE" = "$PROJECT" ] && pass "gcloud project" "$ACTIVE" \
                           || warn "gcloud project" "$ACTIVE (ожидался $PROJECT)"

# 3. Модель >=3.5 доступна и отвечает
if [ -n "${TOKEN:-}" ]; then
  MODELS=$(curl -s -H "Authorization: Bearer $TOKEN" -H "x-goog-user-project: $PROJECT" \
    "https://aiplatform.googleapis.com/v1beta1/publishers/google/models?pageSize=300" \
    | python3 -c 'import sys,json;d=json.load(sys.stdin);print(" ".join(sorted(m["name"].split("/")[-1] for m in d.get("publisherModels",[]) if "gemini" in m.get("name",""))))' 2>/dev/null)
  case "$MODELS" in
    *gemini-3.7-flash*) pass "gemini-3.7-flash в каталоге" "есть" ;;
    *)                  fail "gemini-3.7-flash в каталоге" "НЕТ — проверить регион/проект" ;;
  esac
  # появилась ли наконец Pro >=3.5? (правила требуют 3.5+, Pro пока обрывается на 3.1)
  case "$MODELS" in
    *gemini-3.5-pro*|*gemini-3.6-pro*|*gemini-3.7-pro*)
      warn "Pro >=3.5" "ПОЯВИЛАСЬ — пересмотреть решение D-01!" ;;
    *)  say  "Pro >=3.5" "— нет (как и было, D-01 в силе)" ;;
  esac
fi

# 4. Живой вызов через SDK
python3 - <<'PY' 2>/dev/null && pass "живой вызов SDK" "отвечает" || fail "живой вызов SDK" "не отвечает"
from google import genai
from google.genai import types
c = genai.Client(vertexai=True, project="merci-prod", location="global")
r = c.models.generate_content(model="gemini-3.7-flash", contents="Reply with exactly: OK",
                              config=types.GenerateContentConfig(max_output_tokens=2000))
assert r.text and "OK" in r.text
PY

# 5. Зависимости харнеса
python3 -c "import yaml, google.genai" 2>/dev/null \
  && pass "зависимости (pyyaml, google-genai)" "на месте" \
  || fail "зависимости" "pip install pyyaml google-genai"

# 6. Целостность эталонов и входов
for f in eval/ground_truth/mcdonald-2026.yaml \
         eval/inputs/mcdonald-2026.abstract.md \
         eval/inputs/mcdonald-2026.with_appendix.md \
         eval/prompts/critic_v1.md; do
  [ -f "$f" ] && pass "$(basename "$f")" "на месте" || fail "$(basename "$f")" "ОТСУТСТВУЕТ"
done

# 7. Ключевая проверка эксперимента: во входах A и C не должно быть appendix-чисел.
#    Смотрим на ТЕКСТ ПОСЛЕ вырезания html-комментариев — именно его видит модель
#    (в комментариях-шапках эти числа перечислены намеренно, как список запрещённых).
python3 - <<'PYCHECK' && pass "чистота входов A и C" "appendix-чисел нет" || fail "чистота входов A и C" "числа приложения просочились — эксперимент невалиден"
import re, pathlib, sys
BAD = r'\b(19\.7|10\.4|13\.0|7\.3|13\.9|7\.1|9\.2|7\.8)\b'
bad = False
for f in ("abstract", "fulltext_no_appendix"):
    p = pathlib.Path(f"eval/inputs/mcdonald-2026.{f}.md")
    if not p.exists():
        continue
    clean = re.sub(r"<!--.*?-->", "", p.read_text(), flags=re.DOTALL)
    hits = re.findall(BAD, clean)
    if hits:
        print(f"  {f}: {hits}", file=sys.stderr); bad = True
sys.exit(1 if bad else 0)
PYCHECK

# 8. Дедлайн
python3 - <<'PY'
import datetime as dt
dl = dt.datetime(2026, 9, 1, 2, 0)          # 01.09.2026 02:00 CEST = 31.08 17:00 PDT
left = dl - dt.datetime.now()
h = left.total_seconds() / 3600
print(f'{"до дедлайна":<46} {"⏳"} {left.days} д {int(h % 24)} ч ({h:.0f} ч всего)')
PY

echo "---"
echo "пройдено: $ok · провалено: $bad"
[ "$bad" -eq 0 ] || echo "⚠️  есть провалы — чинить до запуска харнеса"
