# Conectar @sebacoler a la API de Instagram (≈15 min, una sola vez)

> **Nota (nov-2026):** desde finales de octubre de 2026 Meta ya no ofrece "API setup with Instagram login" a las
> apps nuevas: las obliga a usar **"API setup with Unified login"**. Esta guía sigue ese camino nuevo. El token
> es de *usuario del sistema* (SUAT, empieza por `EAA…`), va contra `graph.facebook.com` y no hay que renovarlo
> cada 60 días. Si alguna vez usás una app antigua con Instagram login, poné `IG_LOGIN=instagram` en `.env`.

Requisito: @sebacoler tiene que ser **cuenta profesional (Creador o Empresa) y pública**.

## 1. Crear la app
1. **developers.facebook.com → Mis apps → Crear app**.
2. Nombre (por ejemplo `trials-sebas`) y email.
3. Caso de uso: **"Administrar mensajes y contenido en Instagram"**.
4. Crear app.

## 2. Permisos
**Casos de uso → Personalizar → Permisos y funciones**: que estén agregados
`instagram_business_basic`, `instagram_business_content_publish` e `instagram_business_manage_insights`.

## 3. Tester
1. **Roles de la app → Roles → Agregar personas → Evaluador de Instagram** → `sebacoler`.
2. **instagram.com → Configuración → Apps y sitios web → Invitaciones de evaluador → Aceptar**.

## 4. Configuración del login unificado
**Casos de uso → Personalizar → API setup with Unified login → 1. Configurar tu app**:
1. Crear configuración. Token: **usuario del sistema (SUAT)**; vencimiento: **Nunca**, si te deja elegir.
2. **Editar permisos**: `instagram_basic`, `instagram_manage_messages` (obligatorio),
   `instagram_content_publish` y `instagram_manage_insights`.
3. **Usar esta configuración**. Elegí un portfolio comercial si te lo pide.
4. URL de redireccionamiento: `https://aprendes.ai/`. Es obligatoria pero no la usamos. Guardar.

## 5. Generar el token
**2. Generar tokens de acceso** → al lado de `sebacoler` → **Generar token** → login → copiá el token `EAA…`.
Meta lo muestra **una sola vez**. Guardalo en tu gestor de contraseñas y después pegalo **solo** en el `.env`
del VPS (`IG_TOKEN=`). Nunca en chats ni en GitHub.

El ID de la cuenta (`17841402384432139`) ya viene puesto en `.env.example`.

## 6. Clave de Supabase
Supabase → proyecto **ContentGenerator** → **Project Settings → API Keys** → **secret key** (`sb_secret_…`)
→ `SUPABASE_SERVICE_KEY=` en el `.env` del VPS.

## Si algo falla
| Síntoma | Causa probable |
|---|---|
| `Invalid OAuth access token` / código 190 | Token mal copiado, revocado o caducado. Generá uno nuevo (paso 5). |
| `El token no tiene ninguna cuenta de Instagram asignada` | En el login del paso 5 no quedó marcada @sebacoler. Repetilo. |
| `Application does not have permission` | Falta `instagram_content_publish` o `instagram_manage_insights` en la configuración (paso 4.2). Editala y generá un token nuevo. |
| La cuenta no aparece al añadirla | No aceptaste la invitación de evaluador (paso 3.2) o la cuenta no es profesional. |
