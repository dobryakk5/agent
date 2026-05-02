import asyncio
from typing import Any

from docker_manager import get_container_state


async def record_instance_runtime_state(
    pool,
    user_id: int,
    state: dict[str, Any],
    *,
    gateway_state: str = "not_checked",
    gateway_ready: bool = False,
    last_error: str | None = None,
) -> None:
    """Persist the latest known Docker/Gateway state for one user's container."""
    await pool.execute(
        """
        INSERT INTO instance_runtime_state (
            user_id, container_name, container_id,
            docker_exists, docker_state, docker_started,
            docker_has_ip, docker_ip,
            gateway_state, gateway_ready,
            last_checked_at, last_started_at, last_ready_at,
            last_error, updated_at
        )
        VALUES (
            $1, $2, $3,
            $4, $5, $6,
            $7, $8,
            $9, $10,
            now(),
            CASE WHEN $6 THEN now() ELSE NULL END,
            CASE WHEN $10 THEN now() ELSE NULL END,
            $11,
            now()
        )
        ON CONFLICT (user_id) DO UPDATE
        SET container_name = EXCLUDED.container_name,
            container_id = EXCLUDED.container_id,
            docker_exists = EXCLUDED.docker_exists,
            docker_state = EXCLUDED.docker_state,
            docker_started = EXCLUDED.docker_started,
            docker_has_ip = EXCLUDED.docker_has_ip,
            docker_ip = EXCLUDED.docker_ip,
            gateway_state = EXCLUDED.gateway_state,
            gateway_ready = EXCLUDED.gateway_ready,
            last_checked_at = now(),
            last_started_at = CASE
                WHEN EXCLUDED.docker_started
                     AND (
                        NOT instance_runtime_state.docker_started
                        OR instance_runtime_state.last_started_at IS NULL
                        OR EXCLUDED.container_id IS DISTINCT FROM instance_runtime_state.container_id
                     )
                    THEN now()
                ELSE instance_runtime_state.last_started_at
            END,
            last_ready_at = CASE
                WHEN EXCLUDED.gateway_ready THEN now()
                ELSE instance_runtime_state.last_ready_at
            END,
            last_error = EXCLUDED.last_error,
            updated_at = now()
        """,
        user_id,
        state.get("container_name"),
        state.get("container_id"),
        bool(state.get("exists")),
        state.get("status") or "unknown",
        bool(state.get("running")),
        bool(state.get("has_ip")),
        state.get("ip"),
        gateway_state,
        gateway_ready,
        last_error or state.get("error"),
    )


async def refresh_instance_runtime_state(
    pool,
    user_id: int,
    *,
    gateway_state: str = "not_checked",
    gateway_ready: bool = False,
    last_error: str | None = None,
) -> dict[str, Any]:
    """Inspect Docker now and persist the resulting runtime state."""
    loop = asyncio.get_running_loop()
    state = await loop.run_in_executor(None, lambda: get_container_state(user_id))
    await record_instance_runtime_state(
        pool,
        user_id,
        state,
        gateway_state=gateway_state,
        gateway_ready=gateway_ready,
        last_error=last_error or state.get("error"),
    )
    return state


async def refresh_instance_runtime_state_safe(
    pool,
    user_id: int,
    *,
    gateway_state: str = "not_checked",
    gateway_ready: bool = False,
    last_error: str | None = None,
) -> dict[str, Any] | None:
    """Best-effort refresh for non-critical paths such as UI start/stop."""
    try:
        return await refresh_instance_runtime_state(
            pool,
            user_id,
            gateway_state=gateway_state,
            gateway_ready=gateway_ready,
            last_error=last_error,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[runtime-state] could not refresh user {user_id}: {exc}")
        return None
