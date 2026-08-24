-- OmniCoord IA — V3 / Phase 2
-- Durcissement sécurité Supabase observé à partir des captures du 24/08/2026.
-- À exécuter d'abord sur un environnement de test si possible.
-- Cette migration ne supprime aucune donnée.

begin;

-- ---------------------------------------------------------------------------
-- 1) Helpers RLS : sortir la lecture de profils de sa propre RLS.
--
-- Les versions observées étaient SECURITY INVOKER et lisaient public.profils,
-- alors que les policies de profils appelaient elles-mêmes is_admin().
-- SECURITY DEFINER évite la récursion RLS et garde auth.uid() comme identité.
-- ---------------------------------------------------------------------------
create or replace function public.get_structure_id()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
    select p.structure_id
    from public.profils as p
    where p.id = auth.uid()
    limit 1;
$$;

create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select coalesce((
        select p.est_admin
        from public.profils as p
        where p.id = auth.uid()
        limit 1
    ), false);
$$;

revoke all on function public.get_structure_id() from public;
revoke all on function public.is_admin() from public;
grant execute on function public.get_structure_id() to authenticated;
grant execute on function public.is_admin() to authenticated;

-- ---------------------------------------------------------------------------
-- 2) Profil : la ligne propre reste modifiable, mais le résultat de l'UPDATE
-- doit rester la ligne propre (ou être réalisé par un admin).
-- ---------------------------------------------------------------------------
drop policy if exists profils_update_own on public.profils;
create policy profils_update_own
on public.profils
for update
to authenticated
using ((id = auth.uid()) or public.is_admin())
with check ((id = auth.uid()) or public.is_admin());

-- Harmonise aussi le SELECT observé et évite l'application au rôle anon.
drop policy if exists profils_select_own on public.profils;
create policy profils_select_own
on public.profils
for select
to authenticated
using ((id = auth.uid()) or public.is_admin());

-- ---------------------------------------------------------------------------
-- 3) Colonnes sensibles de profils : RLS filtre les LIGNES, pas les COLONNES.
-- Un utilisateur normal ne doit jamais pouvoir modifier structure_id,
-- est_admin, abonnement, quotas, email d'identité, etc.
--
-- Par défaut on n'autorise en self-service que la configuration mail connue
-- de l'app V2. Toute nouvelle colonne de profil restera protégée par défaut.
-- ---------------------------------------------------------------------------
create or replace function public.protect_profile_security_fields()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    -- L'admin plateforme peut administrer les profils.
    if public.is_admin() then
        return new;
    end if;

    -- Pour un utilisateur normal, seuls les champs de configuration mail et
    -- updated_at peuvent différer. La comparaison jsonb protège aussi les
    -- futures colonnes non encore connues du code actuel.
    if (
        to_jsonb(new) - array[
            'mail_smtp_email',
            'mail_smtp_password',
            'mail_imap_server',
            'updated_at'
        ]::text[]
    ) is distinct from (
        to_jsonb(old) - array[
            'mail_smtp_email',
            'mail_smtp_password',
            'mail_imap_server',
            'updated_at'
        ]::text[]
    ) then
        raise exception 'Modification de champs de sécurité du profil interdite'
            using errcode = '42501';
    end if;

    return new;
end;
$$;

revoke all on function public.protect_profile_security_fields() from public;

-- Nom volontairement préfixé 00_ pour passer avant le trigger updated_at ;
-- updated_at est de toute façon exclu de la comparaison ci-dessus.
drop trigger if exists trg_00_protect_profils_security on public.profils;
create trigger trg_00_protect_profils_security
before update on public.profils
for each row
execute function public.protect_profile_security_fields();

-- ---------------------------------------------------------------------------
-- 4) Journal d'audit : l'application écrit déjà via service_role.
-- On retire donc l'écriture directe aux clients anon/authenticated.
-- Une policy permissive INSERT ne suffit plus si le rôle n'a pas le privilège.
-- ---------------------------------------------------------------------------
revoke insert, update, delete on table public.audit_logs from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 5) Quota IA atomique.
--
-- La V2 faisait : lire quota -> appel Gemini -> incrémenter. Deux requêtes
-- concurrentes pouvaient toutes deux passer. Cette RPC réserve une unité dans
-- un seul UPDATE PostgreSQL atomique AVANT l'appel IA.
-- ---------------------------------------------------------------------------
create or replace function public.reserve_ai_quota()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
    v_used integer;
    v_quota integer;
    v_status text;
begin
    if v_uid is null then
        return jsonb_build_object('allowed', false, 'used', 0, 'quota', 0);
    end if;

    update public.profils
    set nb_requetes_ia = coalesce(nb_requetes_ia, 0) + 1
    where id = v_uid
      and (
          statut_abonnement = 'PRO'
          or coalesce(nb_requetes_ia, 0) < coalesce(quota_max_ia, 0)
      )
    returning nb_requetes_ia, quota_max_ia, statut_abonnement
      into v_used, v_quota, v_status;

    if found then
        return jsonb_build_object(
            'allowed', true,
            'used', coalesce(v_used, 0),
            'quota', coalesce(v_quota, 0),
            'status', v_status
        );
    end if;

    select coalesce(nb_requetes_ia, 0), coalesce(quota_max_ia, 0), statut_abonnement
      into v_used, v_quota, v_status
    from public.profils
    where id = v_uid;

    return jsonb_build_object(
        'allowed', false,
        'used', coalesce(v_used, 0),
        'quota', coalesce(v_quota, 0),
        'status', v_status
    );
end;
$$;

revoke all on function public.reserve_ai_quota() from public, anon;
grant execute on function public.reserve_ai_quota() to authenticated;

commit;

-- Vérification rapide après exécution :
-- select proname, prosecdef
-- from pg_proc
-- where proname in ('get_structure_id', 'is_admin', 'reserve_ai_quota');
--
-- select polname, polcmd, pg_get_expr(polqual, polrelid) as using_expr,
--        pg_get_expr(polwithcheck, polrelid) as check_expr
-- from pg_policy
-- where polrelid = 'public.profils'::regclass;
