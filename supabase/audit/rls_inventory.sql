-- Lecture seule : inventaire de sécurité à copier/coller dans le SQL Editor.
-- Aucun changement de base.

-- 1) RLS activé + policies complètes
select
    n.nspname as schema_name,
    c.relname as table_name,
    c.relrowsecurity as rls_enabled,
    p.polname as policy_name,
    p.polcmd as command,
    pg_get_expr(p.polqual, p.polrelid) as using_expression,
    pg_get_expr(p.polwithcheck, p.polrelid) as with_check_expression,
    array_to_string(array(select rolname from pg_roles where oid = any(p.polroles)), ', ') as roles
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
left join pg_policy p on p.polrelid = c.oid
where n.nspname = 'public'
  and c.relkind = 'r'
order by c.relname, p.polname;

-- 2) Fonctions de sécurité / RPC
select
    n.nspname as schema_name,
    p.proname as function_name,
    pg_get_function_identity_arguments(p.oid) as arguments,
    pg_get_function_result(p.oid) as return_type,
    p.prosecdef as security_definer,
    p.provolatile as volatility,
    p.proconfig as function_settings
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
order by p.proname;

-- 3) Vues : security_invoker doit être vérifié pour les vues qui exposent
-- des données multi-tenant. reloptions contient 'security_invoker=true' si activé.
select
    n.nspname as schema_name,
    c.relname as view_name,
    c.reloptions
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public'
  and c.relkind = 'v'
order by c.relname;

-- 4) Privilèges sensibles sur audit_logs / profils
select grantee, table_name, privilege_type
from information_schema.role_table_grants
where table_schema = 'public'
  and table_name in ('audit_logs', 'profils')
order by table_name, grantee, privilege_type;
