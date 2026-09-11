"""Prepare pinned upstream stacks and private local credentials; never run Docker."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import secrets
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parent.parent
NETWORK = "netbox-generator-link"
NETBOX_COMMIT = "c7092cd0a729ebfbd1c6bce59e09df4953face24"  # netbox-docker 5.1.0
DIODE_COMMIT = "960fcb418892c36cbee4d4faad0e29b9b0bcd2e2"  # Diode 2.2.0
LOCAL_IMAGE = "devin-generator-netbox:4.7.0-diode-1.17.0"
DOCKERIGNORE = "*\n!Dockerfile\n!configuration/\n!configuration/plugins.py\n"
IMAGES = {
    "netbox": "netboxcommunity/netbox:v4.7.0-5.1.0@sha256:a2cdf00fab61d2ae37e4f987adaa403fad5c4049a63bc960768b7bbf804e2cb6",
    "diode-auth": "netboxlabs/diode-auth:2.2.0@sha256:9ba67ea098f53cd80f7136dece4062f17a2922347db3fe9bc0718bf6fe1f0f78",
    "diode-ingester": "netboxlabs/diode-ingester:2.2.0@sha256:e91559769d3ac707057d9a95f94425a7c33d65149de84a401631b666292adbe1",
    "diode-reconciler": "netboxlabs/diode-reconciler:2.2.0@sha256:83638f26d37fe41bb9e7583aca052f3008eee437d8e34c865517aeaf336a3c88",
    "hydra": "oryd/hydra:v26.2.0@sha256:ff67c7fb5f95074fa53374d41151713554960504b340cd3f95b09e65deaea2a9",
    "ingress-nginx": "nginx:latest@sha256:05b8cb60c354a44ab824ea6e7dc69b46d50762cdbe728a347a5b656e6fb3d7c4",
    "diode-redis": "redis/redis-stack-server:latest@sha256:798ab84d9f266936b034ab11c4d04a2b8e4b441884c5aa7d17ac951eefdf742a",
    "diode-postgres": "postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685",
    "netbox-postgres": "postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2",
    "netbox-redis": "valkey/valkey:9.1-alpine@sha256:a0dbf4c1d5708782907c10e2c72deff317518518b5288a58416981d9db95d30b",
}


def write(path, value, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    # The outer target directory is 0700; individual non-secret mounts must be
    # readable by unprivileged container users.
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600 if private else 0o644)
        stream.write(value)


def env_text(values):
    if any("\n" in str(v) or "\r" in str(v) for v in values.values()):
        raise ValueError("Environment values must fit on one line")
    return "".join(f"{key}={value}\n" for key, value in values.items())


def patched_bootstrap(source):
    """Keep the upstream readiness/existence checks; send client JSON on stdin."""
    pattern = r"    client_output=\$\(hydra create oauth2-client .*?      --format json\)"
    replacement = (
        '    printf \'%s\\n\' "$client" | hydra create oauth2-client '
        '--endpoint "$HYDRA_ADMIN_URL" --id "$client_id" --file - >/dev/null 2>&1'
    )
    result, count = re.subn(pattern, lambda _: replacement, source, flags=re.S)
    if count != 1:
        raise ValueError("Pinned Diode bootstrap shape changed; inspect before adapting")
    return result


def redis_command(binary, arguments):
    # Upstream supplies --requirepass in process arguments. A private config
    # file keeps the same authentication while avoiding that exposure.
    return ["sh", "-ec", 'umask 077; printf \'requirepass %s\\n\' "$REDIS_PASSWORD" '
            f'> /tmp/local-auth.conf; exec {binary} /tmp/local-auth.conf {arguments}']


def _front_port_patch():
    applier = ROOT / "lab/front_port_compat.py"
    helper = ROOT / "lab/patches/front_port_compat.py"
    # This is trusted project code; __main__ is not executed. The applier itself
    # checks the installed upstream plugin's exact source hashes during build.
    definition = runpy.run_path(str(applier))
    if definition.get("PLUGIN_VERSION") != "1.17.0" or definition.get("NETBOX_VERSION") != "4.7.0":
        raise ValueError("Front-port patch is not for the pinned NetBox/plugin versions")
    applier_bytes, helper_bytes = applier.read_bytes(), helper.read_bytes()
    applier_hash = hashlib.sha256(applier_bytes).hexdigest()
    helper_hash = hashlib.sha256(helper_bytes).hexdigest()
    if helper_hash != definition["HELPER_SHA256"]:
        raise ValueError("Front-port helper differs from its pinned patch hash")
    record = dict(patch_id=definition["PATCH_ID"], plugin_version="1.17.0", netbox_version="4.7.0",
                  applier_sha256=applier_hash, helper_sha256=helper_hash,
                  source_sha256=definition["SOURCE_SHA256"],
                  output_sha256=definition["OUTPUT_SHA256"],
                  source_url=definition["SOURCE_URL"],
                  sha256=hashlib.sha256(applier_bytes + helper_bytes).hexdigest(),
                  scope="Local NetBox 4.7 qualification only; prepared build inputs, not observed runtime")
    return record, applier_bytes.decode(), helper_bytes.decode()


def _replace_public(path, text):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(text)
    try:
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def stage_front_port_compat(target):
    """Opt in by changing only public build inputs; never rotate secrets or run Docker."""
    target = Path(target).absolute()
    record, applier, helper = _front_port_patch()
    config = json.loads((target / "target.json").read_text())
    required = dict(schema_version=1, docker_context="colima-netbox-generator", docker_network=NETWORK,
                    netbox_version="4.7.0", diode_version="2.2.0", plugin_version="1.17.0")
    if any(config.get(key) != value for key, value in required.items()):
        raise ValueError("Front-port compatibility requires the pinned prepared local target")
    compatibility = config.get("local_compatibility", {})
    if compatibility and compatibility != {"front_port_mapping": record}:
        raise ValueError("Target contains a different local compatibility patch; inspect before changing it")
    directory = target / "netbox"
    base_dockerfile = (ROOT / "lab/Dockerfile").read_text()
    image = f"{LOCAL_IMAGE}-front-ports-{record['sha256'][:12]}"
    label = "org.netboxlabs.devin-generator.front-port-compat"
    dockerfile = base_dockerfile + (
        "\n# Explicit local qualification patch; upstream source hashes are checked during build.\n"
        "COPY front_port_compat.py /build/front_port_compat.py\n"
        "COPY front_port_compat_helper.py /build/front_port_compat_helper.py\n"
        "RUN /opt/netbox/venv/bin/python /build/front_port_compat.py "
        "--plugin-root \"$(/opt/netbox/venv/bin/python -c 'from importlib.util import find_spec; "
        "print(next(iter(find_spec(\"netbox_diode_plugin\").submodule_search_locations)))')\" "
        "--helper /build/front_port_compat_helper.py\n"
        f"LABEL {label}=\"{record['patch_id']}\" {label}-sha256=\"{record['sha256']}\"\n"
    )
    dockerignore = DOCKERIGNORE + "!front_port_compat.py\n!front_port_compat_helper.py\n"
    # Validate every existing input before writing any file, including partial
    # stages from an interrupted attempt. An identical stage is safe to repeat.
    for name, original, changed in (("Dockerfile", base_dockerfile, dockerfile),
                                     (".dockerignore", DOCKERIGNORE, dockerignore)):
        if (directory / name).read_text() not in (original, changed):
            raise ValueError(f"Existing {name} differs from the pinned local build inputs")
    for name, expected in (("front_port_compat.py", applier), ("front_port_compat_helper.py", helper)):
        path = directory / name
        if path.exists() and path.read_text() != expected:
            raise ValueError(f"Existing {name} differs from the requested patch")
    compose = json.loads((directory / "compose.local.json").read_text())
    for name in ("netbox", "netbox-worker"):
        service = compose["services"][name]
        if service["image"] not in (LOCAL_IMAGE, image):
            raise ValueError(f"Existing {name} image differs from the pinned local build")
        service["image"] = image
    sources = json.loads((target / "sources.json").read_text())
    sources["local_compatibility"] = {"front_port_mapping": record}
    config["local_compatibility"] = {"front_port_mapping": record}
    for name, text in (("front_port_compat.py", applier), ("front_port_compat_helper.py", helper),
                       ("Dockerfile", dockerfile), (".dockerignore", dockerignore),
                       ("compose.local.json", json.dumps(compose, indent=2) + "\n")):
        _replace_public(directory / name, text)
    _replace_public(target / "sources.json", json.dumps(sources, indent=2) + "\n")
    _replace_public(target / "target.json", json.dumps(config, indent=2) + "\n")
    return target


def prepare(target, front_port_compat=False):
    target = Path(target).absolute()
    if target.exists():
        if front_port_compat:
            return stage_front_port_compat(target)
        raise ValueError(f"Target already exists: {target}. Reuse it; setup never rotates live credentials.")
    if front_port_compat:
        _front_port_patch()  # Validate opt-in sources before creating a destination.
    sources = []
    fetched = {}
    files = {
        "netbox": (
            "netbox-community/netbox-docker", NETBOX_COMMIT,
            ["docker-compose.yml", "configuration/configuration.py", "configuration/extra.py",
             "configuration/logging.py", "configuration/plugins.py", "env/netbox.env",
             "env/postgres.env", "env/redis.env", "env/redis-cache.env", "LICENSE"]),
        "diode": (
            "netboxlabs/diode", DIODE_COMMIT,
            ["diode-server/docker/docker-compose.yaml", "diode-server/docker/sample.env",
             "diode-server/docker/nginx/nginx.conf", "diode-server/docker/oauth2/bootstrap-clients.sh",
             "LICENSE.md"]),
    }
    # Fetch before creating the destination, so network failures leave no
    # partial target or generated credentials behind.
    for stack, (repo, commit, paths) in files.items():
        for path in paths:
            url = f"https://raw.githubusercontent.com/{repo}/{commit}/{path}"
            with urllib.request.urlopen(url, timeout=30) as response:
                raw = response.read()
            fetched[(stack, path)] = raw.decode()
            sources.append({"url": url, "sha256": hashlib.sha256(raw).hexdigest()})
    bootstrap = patched_bootstrap(fetched[("diode", "diode-server/docker/oauth2/bootstrap-clients.sh")])
    target.mkdir(parents=True, mode=0o700)
    os.chmod(target, 0o700)
    for (stack, path), text in fetched.items():
        write(target / "upstream" / stack / path, text)
    for name in ("configuration.py", "extra.py", "logging.py"):
        write(target / "netbox/configuration" / name, fetched[("netbox", f"configuration/{name}")])
    for name in ("Dockerfile", "plugins.py"):
        dest = target / "netbox" / ("configuration/plugins.py" if name == "plugins.py" else name)
        write(dest, (ROOT / "lab" / name).read_text())
    write(target / "netbox/.dockerignore", DOCKERIGNORE)
    write(target / "netbox/docker-compose.yml", fetched[("netbox", "docker-compose.yml")])
    write(target / "diode/docker-compose.yaml", fetched[("diode", "diode-server/docker/docker-compose.yaml")])
    write(target / "diode/nginx/nginx.conf", fetched[("diode", "diode-server/docker/nginx/nginx.conf")])
    write(target / "diode/oauth2/bootstrap-clients.sh", bootstrap)

    clients = [dict(client_id=name, client_secret=secrets.token_hex(32), scope=scope,
                    grant_types=["client_credentials"], response_types=["token"],
                    token_endpoint_auth_method="client_secret_post")
               for name, scope in [("diode-ingest", "diode:ingest"),
                                   ("diode-to-netbox", "netbox:read netbox:write"),
                                   ("netbox-to-diode", "diode:read diode:write")]]
    client_secrets = {c["client_id"]: c["client_secret"] for c in clients}
    write(target / "diode/oauth2/client/client-credentials.json", json.dumps(clients, indent=2) + "\n", private=True)
    password, db_password, redis_password, cache_password = [secrets.token_hex(32) for _ in range(4)]
    netbox_env = dict(line.split("=", 1) for line in fetched[("netbox", "env/netbox.env")].splitlines()
                      if line and not line.startswith("#") and "=" in line)
    netbox_env.update(DB_PASSWORD=db_password, REDIS_PASSWORD=redis_password,
                     REDIS_CACHE_PASSWORD=cache_password, SECRET_KEY=secrets.token_hex(48),
                     API_TOKEN_PEPPER_1=secrets.token_hex(32), SKIP_SUPERUSER="false",
                     SUPERUSER_NAME="admin", SUPERUSER_EMAIL="admin@example.invalid", SUPERUSER_PASSWORD=password,
                     NETBOX_TO_DIODE_CLIENT_SECRET=client_secrets["netbox-to-diode"],
                     ALLOWED_HOSTS="localhost 127.0.0.1 netbox", CORS_ORIGIN_ALLOW_ALL="False",
                     CSRF_TRUSTED_ORIGINS="http://localhost:8000 http://127.0.0.1:8000",
                     GRANIAN_WORKERS="2", CENSUS_REPORTING_ENABLED="False", MAX_DB_WAIT_TIME="180")
    write(target / "netbox/env/netbox.env", env_text(netbox_env), private=True)
    write(target / "netbox/env/postgres.env", env_text(dict(POSTGRES_DB="netbox", POSTGRES_USER="netbox", POSTGRES_PASSWORD=db_password)), private=True)
    write(target / "netbox/env/redis.env", env_text(dict(REDIS_PASSWORD=redis_password)), private=True)
    write(target / "netbox/env/redis-cache.env", env_text(dict(REDIS_PASSWORD=cache_password)), private=True)
    write(target / "netbox/.env", "COMPOSE_PROJECT_NAME=netbox-generator-netbox\n", private=True)
    diode_env = dict(line.split("=", 1) for line in fetched[("diode", "diode-server/docker/sample.env")].splitlines()
                     if line and not line.startswith("#") and "=" in line)
    for key, value in diode_env.items():
        if value == "<PLACEHOLDER_SECRET>":
            diode_env[key] = secrets.token_hex(32)
    diode_env.update(PROJECT_NAME="netbox-generator-diode", DIODE_TAG="2.2.0",
                     DIODE_NGINX_PORT="127.0.0.1:18080", LOGGING_LEVEL="INFO",
                     DIODE_TO_NETBOX_CLIENT_SECRET=client_secrets["diode-to-netbox"],
                     NETBOX_DIODE_PLUGIN_API_BASE_URL="http://netbox:8080/api/plugins/diode")
    write(target / "diode/.env", env_text(diode_env), private=True)
    write(target / "sdk.env", env_text(dict(DIODE_TARGET="grpc://127.0.0.1:18080/diode",
          DIODE_CLIENT_ID="diode-ingest", DIODE_CLIENT_SECRET=client_secrets["diode-ingest"])), private=True)
    write(target / "credentials.json", json.dumps(dict(netbox_url="http://127.0.0.1:8000", username="admin", password=password), indent=2) + "\n", private=True)

    network = {"shared": {"external": True, "name": NETWORK}}
    local_image = LOCAL_IMAGE
    netbox_services = {
        "netbox": {"image": local_image, "build": {"context": "."},
                   "ports": ["127.0.0.1:8000:8080"], "networks": ["default", "shared"],
                   "healthcheck": {"start_period": "180s", "timeout": "5s"}},
        "netbox-worker": {"image": local_image, "networks": ["default", "shared"]},
        "postgres": {"image": IMAGES["netbox-postgres"]},
    }
    for name, args in [("redis", "--appendonly yes"), ("redis-cache", "")]:
        netbox_services[name] = {"image": IMAGES["netbox-redis"], "command": redis_command("valkey-server", args),
                                "healthcheck": {"test": ["CMD-SHELL", 'VALKEYCLI_AUTH="$$REDIS_PASSWORD" valkey-cli ping | grep -qx PONG']}}
    diode_services = {name: {"image": IMAGES[name]} for name in ("diode-auth", "diode-ingester", "diode-reconciler", "hydra", "ingress-nginx")}
    diode_services.update({"hydra-migrate": {"image": IMAGES["hydra"]},
        "postgres": {"image": IMAGES["diode-postgres"]},
        "redis": {"image": IMAGES["diode-redis"], "command": redis_command("redis-server", '--appendonly yes --dir /data --save 60 1 --port "$REDIS_PORT"')},
        "diode-auth-bootstrap": {"image": IMAGES["diode-auth"], "user": "0:0",
            "volumes": ["./oauth2/bootstrap-clients.sh:/etc/config/oauth2/bootstrap-clients.sh:ro"]}})
    for name in ("ingress-nginx", "diode-reconciler"):
        diode_services[name]["networks"] = ["default", "shared"]
    # Compose interpolation must leave runtime shell variables for the container.
    for services in (netbox_services, diode_services):
        for service in services.values():
            if "command" in service:
                service["command"] = [part.replace("$", "$$") for part in service["command"]]
    write(target / "netbox/compose.local.json", json.dumps(dict(services=netbox_services, networks=network), indent=2) + "\n")
    write(target / "diode/compose.local.json", json.dumps(dict(services=diode_services, networks=network), indent=2) + "\n")
    write(target / "sources.json", json.dumps(dict(netbox_docker="5.1.0", netbox="4.7.0", diode="2.2.0", plugin="1.17.0", images=IMAGES, files=sources), indent=2) + "\n")
    write(target / "target.json", json.dumps(dict(schema_version=1,
          docker_context="colima-netbox-generator", docker_network=NETWORK,
          diode_target="grpc://127.0.0.1:18080/diode", netbox_version="4.7.0",
          diode_version="2.2.0", plugin_version="1.17.0"), indent=2) + "\n")
    if front_port_compat:
        stage_front_port_compat(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "build/local-target")
    parser.add_argument("--front-port-compat", action="store_true",
                        help="opt in to local panel compatibility build inputs; may update an existing prepared target without rotating credentials")
    args = parser.parse_args()
    try:
        target = prepare(args.out, args.front_port_compat)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Setup failed: {exc}\n")
    print(f"Prepared {target}; no Docker services were started.")
    if args.front_port_compat:
        print("Local front-port compatibility is staged only; explicitly rebuild and restart the NetBox app services to apply it.")
    print("Private credentials: credentials.json and sdk.env inside that directory.")
    print(f"Create the external Docker network {NETWORK} before starting either stack.")


if __name__ == "__main__":
    main()
