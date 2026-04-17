import docker

client = docker.from_env()


def create_instance(user_id: int, api_key: str, telegram_bot_token: str) -> dict:
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
            "ANTHROPIC_API_KEY": api_key,
            "TELEGRAM_BOT_TOKEN": telegram_bot_token,
        },
        # Сеть нужна для доступа к Telegram API (api.telegram.org)
        # Поэтому контейнер подключаем к двум сетям:
        # - user_X_net для изоляции
        # - bridge (default) для выхода в интернет
        network=network_name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
    )

    # Подключаем к default bridge для доступа к интернету
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
    """Полное удаление контейнера, сети и volume"""
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


def get_raw_stats(container_name: str) -> dict | None:
    try:
        c = client.containers.get(container_name)
        if c.status != "running":
            return None
        return c.stats(stream=False)
    except docker.errors.NotFound:
        return None
