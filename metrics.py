import asyncio
import asyncpg
from docker_manager import get_raw_stats
from runtime_state import refresh_instance_runtime_state_safe

COLLECT_INTERVAL = 30  # секунд
AUTO_STOP_IDLE_MINUTES = int(__import__("os").environ.get("AUTO_STOP_IDLE_MINUTES", "60"))


def parse_stats(stats: dict) -> dict:
    # CPU
    cpu_delta = (
        stats["cpu_stats"]["cpu_usage"]["total_usage"]
        - stats["precpu_stats"]["cpu_usage"]["total_usage"]
    )
    system_delta = (
        stats["cpu_stats"]["system_cpu_usage"]
        - stats["precpu_stats"]["system_cpu_usage"]
    )
    num_cpus = stats["cpu_stats"].get("online_cpus", 1)
    cpu_percent = (
        (cpu_delta / system_delta) * num_cpus * 100.0 if system_delta > 0 else 0.0
    )

    # Memory
    mem_usage = stats["memory_stats"].get("usage", 0) / 1024 / 1024
    mem_limit = stats["memory_stats"].get("limit", 0) / 1024 / 1024

    # Network
    net_rx, net_tx = 0.0, 0.0
    for iface in stats.get("networks", {}).values():
        net_rx += iface.get("rx_bytes", 0)
        net_tx += iface.get("tx_bytes", 0)

    return {
        "cpu_percent": round(cpu_percent, 2),
        "mem_usage_mb": round(mem_usage, 2),
        "mem_limit_mb": round(mem_limit, 2),
        "net_rx_mb": round(net_rx / 1024 / 1024, 4),
        "net_tx_mb": round(net_tx / 1024 / 1024, 4),
    }


async def collect_metrics_loop(db_url: str):
    pool = await asyncpg.create_pool(db_url)
    loop = asyncio.get_running_loop()

    while True:
        try:
            async with pool.acquire() as conn:
                instances = await conn.fetch(
                    "SELECT user_id, container_name FROM user_instances WHERE status = 'running'"
                )

            for row in instances:
                # get_raw_stats is a blocking Docker SDK call — run in executor
                stats = await loop.run_in_executor(
                    None, lambda name=row["container_name"]: get_raw_stats(name)
                )
                if not stats:
                    continue

                parsed = parse_stats(stats)

                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO container_metrics
                            (user_id, container_name, cpu_percent, mem_usage_mb,
                             mem_limit_mb, net_rx_mb, net_tx_mb)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        row["user_id"],
                        row["container_name"],
                        parsed["cpu_percent"],
                        parsed["mem_usage_mb"],
                        parsed["mem_limit_mb"],
                        parsed["net_rx_mb"],
                        parsed["net_tx_mb"],
                    )
        except Exception as e:
            print(f"[metrics] error: {e}")

        await asyncio.sleep(COLLECT_INTERVAL)


async def auto_stop_loop(db_url: str):
    """Stop instances that have been idle (no Telegram activity) for AUTO_STOP_IDLE_MINUTES."""
    from docker_manager import stop_instance  # local import to avoid circular

    pool = await asyncpg.create_pool(db_url)
    loop = asyncio.get_running_loop()

    while True:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT i.user_id
                    FROM user_instances i
                    LEFT JOIN telegram_links l ON l.user_id = i.user_id
                    WHERE i.status = 'running'
                      AND (
                            l.last_seen_at IS NULL
                            OR l.last_seen_at < now() - ($1 * interval '1 minute')
                          )
                    """,
                    AUTO_STOP_IDLE_MINUTES,
                )

            for row in rows:
                try:
                    await loop.run_in_executor(None, lambda uid=row["user_id"]: stop_instance(uid))
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE user_instances SET status='stopped', stopped_at=now() WHERE user_id=$1",
                            row["user_id"],
                        )
                        await refresh_instance_runtime_state_safe(conn, row["user_id"], gateway_state="stopped")
                    print(f"[auto_stop] stopped idle instance for user {row['user_id']}")
                except Exception as e:
                    print(f"[auto_stop] error stopping user {row['user_id']}: {e}")
        except Exception as e:
            print(f"[auto_stop] error: {e}")

        await asyncio.sleep(60)
