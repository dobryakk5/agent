import docker

client = docker.from_env()


def create_instance(
    user_id: int,
    platform: str,
    api_key: str,
    llm_model: str,
    telegram_bot_token: str,
) -> dict:
    volume_name = f"user_{user_id}_data"
    network_name = f"user_{user_id}_net"
    container_name = f"agent_user_{user_id}"

    client.volumes.create(name=volume_name)
    client.networks.create(name=network_name, driver="bridge")

    container = client.containers.run(
        image="openclaw-agent:latest",
        name=container_name,
        mounts=[
            docker.types.Mount(
                target="/workspace",
                source=volume_name,
                type="volume",
            )
        ],
        environment={
            "USER_ID": str(user_id),
            "PLATFORM": platform,
            "API_KEY": api_key,
            "LLM_MODEL": llm_model,
            "TELEGRAM_BOT_TOKEN": telegram_bot_token,
        },
        network=network_name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        healthcheck={
            "test": ["CMD-SHELL", "curl -sf http://127.0.0.1:18789/health || exit 1"],
            "interval": 30_000_000_000,
            "timeout": 5_000_000_000,
            "start_period": 60_000_000_000,
            "retries": 3,
        },
    )

    # Подключаем к bridge для доступа к интернету
    default_net = client.networks.get("bridge")
    default_net.connect(container)

    return {
        "container_name": container_name,
        "network_name": network_name,
        "volume_name": volume_name,
        "container_id": container.short_id,
    }


def stop_instance(user_id: int):
    container_name = f"agent_user_{user_id}"
    try:
        c = client.containers.get(container_name)
        c.stop()
    except docker.errors.NotFound:
        pass


def remove_instance(user_id: int):
    container_name = f"agent_user_{user_id}"
    network_name = f"user_{user_id}_net"
    volume_name = f"user_{user_id}_data"

    try:
        c = client.containers.get(container_name)
        c.stop()
        c.remove()
    except docker.errors.NotFound:
        pass

    try:
        net = client.networks.get(network_name)
        net.remove()
    except docker.errors.NotFound:
        pass

    try:
        vol = client.volumes.get(volume_name)
        vol.remove()
    except docker.errors.NotFound:
        pass


def recreate_container(
    user_id: int,
    platform: str,
    api_key: str,
    llm_model: str,
    telegram_bot_token: str,
) -> dict:
    """Пересоздаёт только контейнер, сохраняя volume и network."""
    volume_name = f"user_{user_id}_data"
    network_name = f"user_{user_id}_net"
    container_name = f"agent_user_{user_id}"

    # Останавливаем и удаляем только контейнер
    try:
        c = client.containers.get(container_name)
        c.stop()
        c.remove()
    except docker.errors.NotFound:
        pass

    # Создаём новый контейнер с теми же volume и network
    container = client.containers.run(
        image="openclaw-agent:latest",
        name=container_name,
        mounts=[
            docker.types.Mount(
                target="/workspace",
                source=volume_name,
                type="volume",
            )
        ],
        environment={
            "USER_ID": str(user_id),
            "PLATFORM": platform,
            "API_KEY": api_key,
            "LLM_MODEL": llm_model,
            "TELEGRAM_BOT_TOKEN": telegram_bot_token,
        },
        network=network_name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        healthcheck={
            "test": ["CMD-SHELL", "curl -sf http://127.0.0.1:18789/health || exit 1"],
            "interval": 30_000_000_000,
            "timeout": 5_000_000_000,
            "start_period": 60_000_000_000,
            "retries": 3,
        },
    )

    default_net = client.networks.get("bridge")
    default_net.connect(container)

    return {
        "container_name": container_name,
        "network_name": network_name,
        "volume_name": volume_name,
        "container_id": container.short_id,
    }


def get_raw_stats(container_name: str) -> dict | None:
    try:
        c = client.containers.get(container_name)
        if c.status != "running":
            return None
        return c.stats(stream=False)
    except docker.errors.NotFound:
        return None
