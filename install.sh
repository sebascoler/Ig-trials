#!/usr/bin/env bash
# Instalador de ig-trials para el VPS (Ubuntu/Debian). Se puede ejecutar varias veces sin romper nada.
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"

echo "→ Comprobando Python 3.10+"
command -v python3 >/dev/null || { sudo apt-get update && sudo apt-get install -y python3 python3-venv; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || { echo "Necesito Python 3.10 o superior"; exit 1; }
python3 -m venv --help >/dev/null 2>&1 || sudo apt-get install -y python3-venv

echo "→ Entorno virtual"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo
  echo "⚠  Creé el archivo .env. Rellená IG_TOKEN, SUPABASE_URL y SUPABASE_SERVICE_KEY:"
  echo "     nano $DIR/.env"
  echo "   y volvé a ejecutar: bash install.sh"
  exit 0
fi
chmod 600 .env
mkdir -p logs

echo "→ Diagnóstico"
.venv/bin/python -m igtrials doctor || { echo "Corregí lo que falla arriba y volvé a ejecutar install.sh"; exit 1; }

echo "→ Programando el cron"
PY="$DIR/.venv/bin/python -m igtrials"
LOCK="flock -n /tmp/igtrials"
BLOQUE=$(cat <<EOF
# >>> ig-trials >>>
*/10 * * * * cd $DIR && $LOCK-publicar.lock $PY publicar >> logs/publicar.log 2>&1
17 * * * *   cd $DIR && $LOCK-metricas.lock $PY metricas >> logs/metricas.log 2>&1
30 9 * * *   cd $DIR && $PY comparar >> logs/comparar.log 2>&1
45 4 * * *   cd $DIR && $PY token >> logs/token.log 2>&1
0 5 * * 0    cd $DIR && find logs -name '*.log' -size +5M -exec truncate -s 1M {} \;
# <<< ig-trials <<<
EOF
)
( crontab -l 2>/dev/null | sed '/# >>> ig-trials >>>/,/# <<< ig-trials <<</d'; echo "$BLOQUE" ) | crontab -
echo "$BLOQUE"

echo
echo "✔ Listo. Publica cada 10 min, mide cada hora, compara a las 9:30 y renueva el token solo."
echo "  Prueba rápida:  .venv/bin/python -m igtrials probar prueba/test.mp4"
echo "  Ver estado:     .venv/bin/python -m igtrials estado"
