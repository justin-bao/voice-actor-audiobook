create extension if not exists pgcrypto;

create table if not exists public.audiobook_projects (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  project_id text not null,
  title text not null,
  language text not null default 'zh',
  narration_mode text not null default 'multi_voice',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_id, project_id)
);

create table if not exists public.audiobook_chapters (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  project_id text not null,
  chapter_id text not null,
  title text not null,
  order_index integer not null default 0,
  char_count integer not null default 0,
  analyzed boolean not null default false,
  annotated boolean not null default false,
  pipeline_state text,
  pipeline_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_id, project_id, chapter_id),
  foreign key (owner_id, project_id)
    references public.audiobook_projects(owner_id, project_id)
    on delete cascade
);

create index if not exists audiobook_chapters_project_order_idx
  on public.audiobook_chapters (owner_id, project_id, order_index);

alter table public.audiobook_projects enable row level security;
alter table public.audiobook_chapters enable row level security;

grant select, insert, update, delete on public.audiobook_projects to authenticated;
grant select, insert, update, delete on public.audiobook_chapters to authenticated;

create policy "owners can manage audiobook projects"
  on public.audiobook_projects
  for all
  to authenticated
  using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);

create policy "owners can manage audiobook chapters"
  on public.audiobook_chapters
  for all
  to authenticated
  using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);

insert into storage.buckets (id, name, public)
values ('audiobook-artifacts', 'audiobook-artifacts', false)
on conflict (id) do nothing;

create policy "owners can read audiobook artifacts"
  on storage.objects
  for select
  to authenticated
  using (
    bucket_id = 'audiobook-artifacts'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

create policy "owners can upload audiobook artifacts"
  on storage.objects
  for insert
  to authenticated
  with check (
    bucket_id = 'audiobook-artifacts'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

create policy "owners can update audiobook artifacts"
  on storage.objects
  for update
  to authenticated
  using (
    bucket_id = 'audiobook-artifacts'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  )
  with check (
    bucket_id = 'audiobook-artifacts'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

create policy "owners can delete audiobook artifacts"
  on storage.objects
  for delete
  to authenticated
  using (
    bucket_id = 'audiobook-artifacts'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );
