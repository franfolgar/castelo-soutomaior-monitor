# Monitor de entradas · Castelo de Soutomaior

Script Python (`monitor.py`) que compara el catálogo de tickets.castelodesoutomaior.com (API Clorian) con `estado_anterior.json` y avisa por Telegram cuando aparecen entradas nuevas.

## Ejecutar
- `pip install -r requirements.txt`
- `python monitor.py` · en Windows: `run_monitor.bat`

## Configuración
`.env` (no versionado; plantilla en `.env.example`): `TARGET_URL`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`.

## Producción
GitHub Actions (`.github/workflows/monitor.yml`) lo ejecuta con los secretos del repo y hace commit de `estado_anterior.json`. Dos disparadores:
- **cron-job.org** (principal): lanza `workflow_dispatch` vía API de GitHub a las :00 y :30. Se configura en cron-job.org, no en el repo.
- **`schedule:` del workflow** (respaldo): :17 y :47; GitHub retrasa o se salta muchas de estas. Antes de tocar el estado o el workflow, haz `git pull`: el bot de CI hace commits a `main`.

## Notas
- Repo **público**: nunca commitear `.env` ni tokens.
- `monitor.log` es local (ignorado).
