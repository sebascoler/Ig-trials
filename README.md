# ig-trials

Publica variantes de un reel como **trial reels** (solo las ven no-seguidores), mide el skip rate de los
primeros 3 s y el watch medio por API, decide qué hook gana y lo apunta en `ganchos.md` para el próximo guion.

Basado en la guía de Gonzalo López («Claude sube tus vídeos de prueba con la API de Instagram»), con tres cambios:

1. **Variantes en paralelo, no una por día.** Todas salen el mismo día con 1 hora de separación, así el día
   de la semana no contamina la comparación (es lo que pide tu skill `testeo-reels`). No bajes de 1 hora:
   con menos, Instagram puede tratarlas como contenido repetido y frenarles el alcance. Con 5 variantes la
   tanda ocupa 4 horas, así que programá el inicio para que todas caigan en una franja buena (por ejemplo, 15:00 → 19:00).
   Si preferís el método original, usá `"modo": "secuencial"` (una por día, siempre a la misma hora).
2. **Comparación a edad fija.** Se guarda una foto de las métricas de cada variante a las 24 h, 72 h y 7 días,
   y se comparan todas a las 72 h.
3. **Ganador solo con dos señales.** Gana quien tenga el menor skip con al menos 5 pts de ventaja **y**
   además lidere en watch medio o en guardados. Las variantes con menos de 300 de alcance no cuentan.
   Si no se cumple, el veredicto es «sin ganador claro», que también es información.

## Cómo está montado

```
Vos/Claude ──► Supabase (ContentGenerator)
               ├─ bucket "reels"      ← los MP4 (URL pública que Instagram descarga)
               ├─ ig_videos/ig_posts  ← el calendario
               ├─ ig_metricas         ← fotos a 24h/72h/7d
               └─ ig_resultados       ← veredictos + ranking de principios
                        ▲
VPS Hostinger (cron) ───┘  publica cada 10 min · mide cada hora · compara 9:30 · vigila el token
```

## Instalación (una vez)
1. Conectá Instagram siguiendo `docs/META_SETUP.md`.
2. En el VPS:
   ```bash
   cd /opt && git clone https://github.com/sebascoler/Ig-trials.git ig-trials   # repo privado: usuario + token de GitHub
   cd /opt/ig-trials
   bash install.sh          # crea .env y se para
   nano .env                # pegá IG_TOKEN y SUPABASE_SERVICE_KEY
   bash install.sh          # comprueba todo y deja el cron programado
   ```
3. Prueba de humo: subí un MP4 cualquiera a `reels/prueba/test.mp4` en Supabase (Storage → reels → Upload) y
   ```bash
   .venv/bin/python -m igtrials probar prueba/test.mp4
   ```
   Sale como trial: tus seguidores no lo ven. Borralo desde la app cuando quieras.

## Uso semanal
1. **Hooks:** `testeo-reels` te da 3-5 variaciones con su tipo de hook (dolor directo, paradoja, promesa, POV,
   contra-corriente). Ese tipo es el `principio` que se guarda y se rankea.
2. **Subir los MP4** al bucket `reels`, por ejemplo `esfuerzo-claude/A.mp4`, `B.mp4`... (máx. 50 MB cada uno
   en el plan gratis de Supabase; un reel de 45 s ocupa bastante menos).
3. **Programar:** pedíselo a Claude en el chat («programá esfuerzo-claude para el martes a las 19:00 con estos
   hooks»), que lo da de alta en Supabase, o con un JSON como `ejemplo-programar.json`:
   ```bash
   .venv/bin/python -m igtrials programar ejemplo-programar.json
   ```
4. **Esperar.** A las 72 h llega el veredicto (en `ganchos.md`, en la tabla `ig_resultados` y, si configuraste
   ntfy, como aviso en el móvil).
5. **Si hay ganador:** abrí ese trial en la app y pasalo a tus seguidores a mano (Meta no permite hacerlo por API).
   O programá la versión ganadora como reel normal (`"normal"` en el JSON).

## Comandos
| Comando | Qué hace |
|---|---|
| `doctor` | Comprueba .env, Supabase y token |
| `estado` | Próximas publicaciones y últimas (con enlaces o errores) |
| `probar <archivo>` | Publica ya un trial de prueba |
| `programar <json>` | Da de alta un vídeo y sus variantes |
| `comparar [slug]` | Fuerza la comparación (normalmente va solo por cron) |
| `token` | Comprueba que el token sigue vivo (y lo renueva en modo `instagram`) |

Si una variante queda en `error` (te llega aviso), corregí el archivo y poné su estado en `pendiente` desde
Supabase, o en `cancelado` para comparar sin ella.

## Límites que conviene saber
- `reels_skip_rate` solo existe por API (en la app no se ve) y Meta lo marca como métrica «estimada, en desarrollo».
- Hay reportes de un límite de unos 20 trial reels al día por API. El sistema se frena en 15 (`IGT_MAX_TRIALS_24H`).
- Las variantes tienen que ser realmente distintas en los primeros segundos. Instagram castiga el contenido duplicado.
- Login: las apps nuevas de Meta usan **login unificado** (token de usuario del sistema, `graph.facebook.com`,
  `v26.0`). El token no se renueva: el cron comprueba cada día que siga vivo y avisa si deja de funcionar.
  Para apps antiguas con Instagram login: `IG_LOGIN=instagram` (token de 60 días, renovado cada semana).
- Que los trial reels (`trial_params`) y `reels_skip_rate` funcionen con el login unificado lo confirma la
  primera prueba real (`probar`). Meta no lo detalla en la documentación del login unificado.

## Tests
`python3 tests/test_flujo.py` simula Instagram y Supabase y prueba el ciclo completo: publicar, reintentar,
medir, comparar, renovar el token y gestionar errores.
