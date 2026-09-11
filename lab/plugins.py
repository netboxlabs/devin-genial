"""Local disposable target; runtime secrets arrive through Compose env_file."""

from os import environ

PLUGINS = ["netbox_diode_plugin"]
PLUGINS_CONFIG = {
    "netbox_diode_plugin": {
        "diode_target": "grpc://ingress-nginx:80/diode",
        "diode_target_display": "grpc://127.0.0.1:18080/diode",
        "netbox_to_diode_client_secret": environ.get("NETBOX_TO_DIODE_CLIENT_SECRET"),
    }
}
