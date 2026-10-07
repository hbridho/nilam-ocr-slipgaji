#!/usr/bin/env bash
# Put a prompt file into Cloud SQL nilam_ocr_slipgaji.system_prompt as the active row (structuring reads it with
# PROMPT_SOURCE=db), then delete the Redis key so the next job uses it without a restart. Idempotent: a file equal to
# the active row inserts nothing. Run as root on the VM, from anywhere:
#
#   deploy/vm/scripts/set_prompt.sh                                   # services/structuring/prompts/slip_gaji.v1.md
#   deploy/vm/scripts/set_prompt.sh /path/slip_gaji.v2.md "v2: what changed"
#
# No secret is printed.
set -euo pipefail
VM_DIR=$(cd "$(dirname "$0")/.." && pwd)
REPO=$(cd "$VM_DIR/../.." && pwd)
PROMPT=$(realpath "${1:-$REPO/services/structuring/prompts/slip_gaji.v1.md}")
NOTE=${2:-"$(basename "$PROMPT")"}
test -s "$PROMPT" || { echo "prompt file missing or empty: $PROMPT" >&2; exit 1; }
cd "$VM_DIR"
C="docker compose -f docker-compose.yml -f docker-compose.build.yml"

echo "== system_prompt <- $PROMPT"
$C --profile migrate run --rm -T -e NOTE="$NOTE" -v "$PROMPT:/prompt/prompt.md:ro" migrate python - <<'PY' 2>&1 | grep -v "^INFO\| Container "
import asyncio, os
from pathlib import Path
from sqlalchemy import text
from ocr_common.pipeline.cloudsql import config_from, register
from ocr_common.pipeline.database import dispose_engines, get_engine

TABLE = "nilam_ocr_slipgaji.system_prompt"
prompt = Path("/prompt/prompt.md").read_text(encoding="utf-8")
url = register(config_from({k.lower(): v for k, v in os.environ.items()}))

async def main():
    async with get_engine(url).begin() as conn:
        active = (await conn.execute(text(f"SELECT version, system_prompt FROM {TABLE} WHERE is_active"))).first()
        if active is not None and active[1] == prompt:
            print(f"already active as version {active[0]}: nothing inserted")
        else:
            await conn.execute(text(f"UPDATE {TABLE} SET is_active = FALSE WHERE is_active"))
            version = (await conn.execute(
                text(f"INSERT INTO {TABLE} (system_prompt, is_active, change_note) VALUES (:p, TRUE, :n) RETURNING version"),
                {"p": prompt, "n": os.environ["NOTE"][:100]},
            )).scalar_one()
            print(f"inserted version {version}, active ({len(prompt)} characters)")
        rows = (await conn.execute(text(
            f"SELECT version, is_active, length(system_prompt), change_note, created_at FROM {TABLE} ORDER BY version"
        ))).all()
    await dispose_engines()
    for r in rows:
        print(f"  version={r[0]} active={r[1]} chars={r[2]} note={r[3]!r} created={r[4]:%Y-%m-%d %H:%M}")

asyncio.run(main())
PY

R=ms-bribrain-nilam-ocr-slipgaji-redis
KEY=$(grep -E '^STRUCTURING_PROMPT_REDIS_KEY=' .env | cut -d= -f2-)
KEY=${KEY:-ocr:prompt:slipgaji}
if docker ps --format '{{.Names}}' | grep -qx "$R"; then
  echo "== Redis: DEL $KEY -> $(docker exec "$R" sh -c "REDISCLI_AUTH=\"\$REDIS_PASSWORD\" redis-cli DEL $KEY") (1 = deleted)"
else
  echo "== no Redis container: restart structuring to load the prompt ($C restart structuring)"
fi
