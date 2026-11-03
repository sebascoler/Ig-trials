# Conectar @sebacoler a la API de Instagram (≈15 min, una sola vez)

Requisito: @sebacoler tiene que ser **cuenta profesional (Creador o Empresa) y pública**.
En la app: Configuración → Tipo de cuenta y herramientas → Cambiar a cuenta profesional.

## 1. Crear la app de Meta
1. Entrá a **developers.facebook.com** con tu Facebook → **Mis apps** → **Crear app**.
2. Caso de uso: **Otro** → Tipo: **Empresa (Business)** → nombre, por ejemplo `ig-trials-sebas`.
3. En el panel de la app: **Añadir producto → Instagram → Configurar**.
   Elegí **"API setup with Instagram login"** (no la variante con Facebook Login).

## 2. Darte permiso como tester
Mientras la app esté en modo desarrollo, solo la usan cuentas con rol:
1. **Roles de la app → Roles → Añadir personas → Instagram Tester** → escribí `sebacoler`.
2. En Instagram (web o app): **Configuración → Apps y sitios web → Invitaciones de tester → Aceptar**.

No hace falta App Review: es tu propia cuenta (acceso estándar).

## 3. Generar el token
1. **Instagram → API setup with Instagram login → 1. Generate access tokens → Add account**.
2. Iniciá sesión con @sebacoler y aceptá los permisos. Tienen que quedar marcados:
   - `instagram_business_basic`
   - `instagram_business_content_publish`
   - `instagram_business_manage_insights` (sin este no hay métricas)
3. Pulsá **Generate token** y copiá el token largo (empieza por `IG…` o `EA…`).

⚠️ El token es como una contraseña: no lo pegues en chats ni en GitHub. Va **solo** en el archivo `.env` del VPS.
Dura 60 días, pero el sistema lo renueva solo cada semana.

## 4. Clave de Supabase
Supabase → proyecto **ContentGenerator** → **Project Settings → API Keys** → copiá la **secret key** (`sb_secret_…`).
También va solo en el `.env` del VPS.

## Si algo falla
| Síntoma | Causa probable |
|---|---|
| `Invalid OAuth access token` / código 190 | Token caducado o mal copiado. Generá uno nuevo (paso 3). |
| `Application does not have permission` | Falta algún permiso del paso 3.2. Volvé a generar el token marcándolo. |
| La cuenta no aparece al añadirla | No aceptaste la invitación de tester (paso 2.2) o la cuenta no es profesional. |
