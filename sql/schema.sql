-- ig-trials: calendario, métricas y resultados de trial reels
-- Todas las tablas con RLS activado y SIN políticas: solo la service key del VPS puede leer/escribir.

create table if not exists public.ig_videos (
  slug        text primary key,
  titulo      text not null,
  modo        text not null default 'paralelo' check (modo in ('paralelo', 'secuencial')),
  notas       text,
  creado_en   timestamptz not null default now()
);

create table if not exists public.ig_posts (
  id                   bigint generated always as identity primary key,
  video_slug           text not null references public.ig_videos(slug) on delete cascade,
  tipo                 text not null default 'trial' check (tipo in ('trial', 'normal')),
  variante             text not null default 'A',          -- A, B, C... ('FEED' para el normal)
  hook                 text,                                -- frase del hook
  principio            text,                                -- dolor directo, paradoja, promesa, POV, contra-corriente...
  archivo              text not null,                       -- ruta dentro del bucket "reels"
  caption              text not null default '',
  publicar_en          timestamptz not null,
  estado               text not null default 'pendiente'
                       check (estado in ('pendiente', 'procesando', 'publicado', 'error', 'cancelado')),
  container_id         text,
  container_creado_en  timestamptz,
  media_id             text,
  permalink            text,
  publicado_en         timestamptz,
  intentos             int not null default 0,
  error                text,
  creado_en            timestamptz not null default now(),
  unique (video_slug, tipo, variante)
);
create index if not exists ig_posts_estado_idx on public.ig_posts (estado, publicar_en);

-- Una foto de las métricas a edad fija (24h, 72h, 168h) para comparar variantes en igualdad de condiciones
create table if not exists public.ig_metricas (
  post_id             bigint not null references public.ig_posts(id) on delete cascade,
  checkpoint_h        int not null,
  medido_en           timestamptz not null default now(),
  views               int,
  reach               int,
  likes               int,
  comments            int,
  saved               int,
  shares              int,
  total_interactions  int,
  avg_watch_ms        int,
  total_watch_ms      bigint,
  skip_rate           numeric,          -- % que se va en los primeros 3 s (0-100)
  raw                 jsonb,
  primary key (post_id, checkpoint_h)
);

create table if not exists public.ig_resultados (
  video_slug        text primary key references public.ig_videos(slug) on delete cascade,
  checkpoint_h      int not null,
  veredicto         text not null check (veredicto in ('ganador', 'sin_ganador_claro', 'datos_insuficientes')),
  ganador_post_id   bigint references public.ig_posts(id),
  principio_ganador text,
  hook_ganador      text,
  resumen           text not null,
  tabla             jsonb,
  decidido_en       timestamptz not null default now()
);

-- Qué principios de hook ganan más a lo largo del tiempo
create or replace view public.ig_ranking_principios
with (security_invoker = true) as
select
  p.principio,
  count(distinct p.video_slug)                                         as tests,
  count(distinct r.video_slug) filter (where r.ganador_post_id = p.id) as victorias,
  round(avg(m.skip_rate), 1)                                           as skip_medio,
  round(avg(m.avg_watch_ms) / 1000.0, 1)                               as watch_medio_s
from public.ig_posts p
join public.ig_resultados r on r.video_slug = p.video_slug
left join public.ig_metricas m on m.post_id = p.id and m.checkpoint_h = r.checkpoint_h
where p.tipo = 'trial' and p.principio is not null
group by p.principio
order by victorias desc, skip_medio asc;

alter table public.ig_videos     enable row level security;
alter table public.ig_posts      enable row level security;
alter table public.ig_metricas   enable row level security;
alter table public.ig_resultados enable row level security;

-- Bucket público para los MP4 (Instagram los descarga desde la URL pública)
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('reels', 'reels', true, 314572800, array['video/mp4', 'video/quicktime'])
on conflict (id) do update set public = true;

-- Que ni anon ni usuarios logueados vean estas tablas (el VPS usa la service key)
revoke all on public.ig_videos, public.ig_posts, public.ig_metricas, public.ig_resultados, public.ig_ranking_principios
  from anon, authenticated;
