import io
import json
import os
import tarfile

import docker

client = docker.from_env()

GOOGLE_OAUTH_JSON_PATH = os.environ.get("GOOGLE_OAUTH_JSON_PATH", "")
GOOGLE_OAUTH_JSON_IN_CONTAINER = "/run/secrets/app/google-oauth.json"
GOOGLE_TOKENS_JSON_IN_CONTAINER = "/run/secrets/user/google-tokens.json"


def ensure_volume(name: str):
    try:
        client.volumes.get(name)
    except docker.errors.NotFound:
        client.volumes.create(name=name)


def ensure_network(name: str):
    try:
        client.networks.get(name)
    except docker.errors.NotFound:
        client.networks.create(name=name, driver="bridge")


def get_secrets_volume_name(user_id: int) -> str:
    return f"user_{user_id}_secrets"


def _get_data_volume_name(user_id: int) -> str:
    return f"user_{user_id}_data"


def _get_network_name(user_id: int) -> str:
    return f"user_{user_id}_net"


def _get_container_name(user_id: int) -> str:
    return f"agent_user_{user_id}"


def write_user_secret_file(user_id: int, filename: str, content: str) -> str:
    """Write a file into the user secrets volume without requiring root on the host.

    Uses Docker's put_archive API: creates a temporary stopped container that
    has the secrets volume mounted, streams a tar archive into it, then removes
    the container. The volume data persists after the container is removed.
    """
    volume_name = get_secrets_volume_name(user_id)
    ensure_volume(volume_name)

    content_bytes = content.encode("utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=filename)
        info.size = len(content_bytes)
        info.mode = 0o600
        tar.addfile(info, io.BytesIO(content_bytes))
    buf.seek(0)

    container = client.containers.create(
        "alpine:3.19",
        mounts=[docker.types.Mount(target="/secrets", source=volume_name, type="volume")],
    )
    try:
        container.put_archive("/secrets", buf.read())
    finally:
        container.remove(force=True)

    return filename


def write_user_secret_json(user_id: int, filename: str, payload: dict) -> str:
    return write_user_secret_file(
        user_id,
        filename,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def delete_user_secret_file(user_id: int, filename: str) -> None:
    """Remove a file from the user secrets volume without requiring root on the host."""
    volume_name = get_secrets_volume_name(user_id)
    try:
        client.containers.run(
            "alpine:3.19",
            command=["rm", "-f", f"/secrets/{filename}"],
            mounts=[docker.types.Mount(target="/secrets", source=volume_name, type="volume")],
            remove=True,
        )
    except Exception:
        pass


def _container_mounts(user_id: int):
    mounts = [
        docker.types.Mount(
            target="/workspace",
            source=_get_data_volume_name(user_id),
            type="volume",
        ),
        docker.types.Mount(
            target="/run/secrets/user",
            source=get_secrets_volume_name(user_id),
            type="volume",
        ),
    ]

    if GOOGLE_OAUTH_JSON_PATH:
        mounts.append(
            docker.types.Mount(
                target=GOOGLE_OAUTH_JSON_IN_CONTAINER,
                source=GOOGLE_OAUTH_JSON_PATH,
                type="bind",
                read_only=True,
            )
        )

    return mounts


def _container_environment(
    *,
    user_id: int,
    platform: str,
    api_key: str,
    llm_model: str,
    gateway_token: str,
):
    return {
        "USER_ID": str(user_id),
        "PLATFORM": platform,
        "API_KEY": api_key,
        "LLM_MODEL": llm_model,
        "GOOGLE_OAUTH_JSON_PATH": GOOGLE_OAUTH_JSON_IN_CONTAINER,
        "GOOGLE_TOKENS_JSON_PATH": GOOGLE_TOKENS_JSON_IN_CONTAINER,
        "GATEWAY_AUTH_TOKEN": gateway_token,
    }


def _run_container(container_name: str, user_id: int, platform: str,
                   api_key: str, llm_model: str, gateway_token: str,
                   network_name: str):
    return client.containers.run(
        image="openclaw-agent:latest",
        name=container_name,
        mounts=_container_mounts(user_id),
        environment=_container_environment(
            user_id=user_id,
            platform=platform,
            api_key=api_key,
            llm_model=llm_model,
            gateway_token=gateway_token,
        ),
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


def create_instance(
    user_id: int,
    platform: str,
    api_key: str,
    llm_model: str,
    gateway_token: str,
) -> dict:
    volume_name = _get_data_volume_name(user_id)
    secrets_volume_name = get_secrets_volume_name(user_id)
    network_name = _get_network_name(user_id)
    container_name = _get_container_name(user_id)

    ensure_volume(volume_name)
    ensure_volume(secrets_volume_name)
    ensure_network(network_name)

    container = _run_container(
        container_name, user_id, platform, api_key, llm_model, gateway_token, network_name
    )

    return {
        "container_name": container_name,
        "network_name": network_name,
        "volume_name": volume_name,
        "secrets_volume_name": secrets_volume_name,
        "container_id": container.short_id,
    }


def stop_instance(user_id: int):
    try:
        c = client.containers.get(_get_container_name(user_id))
        c.stop()
    except docker.errors.NotFound:
        pass


def start_instance(user_id: int):
    container_name = _get_container_name(user_id)
    try:
        c = client.containers.get(container_name)
        c.start()
        return c
    except docker.errors.NotFound:
        raise RuntimeError(f"Container {container_name} not found — provision the instance first")


def remove_instance(user_id: int):
    container_name = _get_container_name(user_id)
    network_name = _get_network_name(user_id)
    volume_name = _get_data_volume_name(user_id)
    secrets_volume_name = get_secrets_volume_name(user_id)

    try:
        c = client.containers.get(container_name)
        c.stop()
        c.remove(force=True)
    except docker.errors.NotFound:
        pass

    try:
        client.networks.get(network_name).remove()
    except docker.errors.NotFound:
        pass

    for volume in (volume_name, secrets_volume_name):
        try:
            client.volumes.get(volume).remove()
        except docker.errors.NotFound:
            pass


def recreate_container(
    user_id: int,
    platform: str,
    api_key: str,
    llm_model: str,
    gateway_token: str,
) -> dict:
    volume_name = _get_data_volume_name(user_id)
    secrets_volume_name = get_secrets_volume_name(user_id)
    network_name = _get_network_name(user_id)
    container_name = _get_container_name(user_id)

    ensure_volume(volume_name)
    ensure_volume(secrets_volume_name)
    ensure_network(network_name)

    try:
        c = client.containers.get(container_name)
        c.stop()
        c.remove(force=True)
    except docker.errors.NotFound:
        pass

    container = _run_container(
        container_name, user_id, platform, api_key, llm_model, gateway_token, network_name
    )

    return {
        "container_name": container_name,
        "network_name": network_name,
        "volume_name": volume_name,
        "secrets_volume_name": secrets_volume_name,
        "container_id": container.short_id,
    }


def get_container_ip(user_id: int) -> str:
    container_name = _get_container_name(user_id)
    network_name = _get_network_name(user_id)
    c = client.containers.get(container_name)
    c.reload()
    networks = c.attrs.get("NetworkSettings", {}).get("Networks", {})
    net = networks.get(network_name) or next(iter(networks.values()), None)
    if not net:
        raise RuntimeError(f"Container {container_name} has no network info")
    ip = net.get("IPAddress")
    if not ip:
        raise RuntimeError(f"Container {container_name} has no IP address")
    return ip


def get_raw_stats(container_name: str) -> dict | None:
    try:
        c = client.containers.get(container_name)
        c.reload()
        if c.status != "running":
            return None
        return c.stats(stream=False)
    except docker.errors.NotFound:
        return None
