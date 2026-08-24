-- OmniCoord IA — quota IA atomique, réservé au serveur
-- À appliquer APRÈS le durcissement Supabase réalisé le 24/08/2026.
-- Ne supprime aucune donnée.

BEGIN;

CREATE OR REPLACE FUNCTION public.reserve_ai_quota(p_user_id uuid)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_used integer := 0;
    v_quota integer := 0;
    v_status text;
BEGIN
    IF p_user_id IS NULL THEN
        RETURN jsonb_build_object('allowed', false, 'used', 0, 'quota', 0);
    END IF;

    UPDATE public.profils
    SET nb_requetes_ia = COALESCE(nb_requetes_ia, 0) + 1
    WHERE id = p_user_id
      AND (
          statut_abonnement = 'PRO'
          OR COALESCE(nb_requetes_ia, 0) < COALESCE(quota_max_ia, 0)
      )
    RETURNING nb_requetes_ia, quota_max_ia, statut_abonnement
      INTO v_used, v_quota, v_status;

    IF FOUND THEN
        RETURN jsonb_build_object(
            'allowed', true,
            'used', COALESCE(v_used, 0),
            'quota', COALESCE(v_quota, 0),
            'status', v_status
        );
    END IF;

    SELECT
        COALESCE(nb_requetes_ia, 0),
        COALESCE(quota_max_ia, 0),
        statut_abonnement
    INTO v_used, v_quota, v_status
    FROM public.profils
    WHERE id = p_user_id;

    RETURN jsonb_build_object(
        'allowed', false,
        'used', COALESCE(v_used, 0),
        'quota', COALESCE(v_quota, 0),
        'status', v_status
    );
END;
$$;

REVOKE ALL ON FUNCTION public.reserve_ai_quota(uuid)
FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.reserve_ai_quota(uuid)
TO service_role;

COMMIT;

-- Contrôle : doit retourner seulement postgres + service_role pour EXECUTE
-- SELECT routine_name, grantee, privilege_type
-- FROM information_schema.routine_privileges
-- WHERE routine_schema='public' AND routine_name='reserve_ai_quota'
-- ORDER BY grantee;
