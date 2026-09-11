"""Apply the opt-in local panel bridge to exactly the pinned Diode plugin source.

No network or NetBox writes. Run during the disposable target's image build,
after installing diode-netbox-plugin==1.17.0. All source guards are checked before
any file changes. A different or already-patched source fails closed.
"""

import argparse
import hashlib
from pathlib import Path


PATCH_ID = "devin-generator-front-port-compat-v1"
PLUGIN_VERSION = "1.17.0"
NETBOX_VERSION = "4.7.0"
HELPER_SHA256 = "a6689bff43aa8df8e84d7ebe99feb70e4d1e811f7eeb4a03e598a98e357fd2ec"
SOURCE_URL = "https://github.com/netboxlabs/diode-netbox-plugin/tree/v1.17.0/netbox_diode_plugin/api"
SOURCE_SHA256 = {
    "transformer.py": "54d47d4232fd5d9b7dcf2ad91f02da4447bd2ee85ce1f6e7761805d664da7d91",
    "differ.py": "ecbedeaa8bd036923cacd2011ff1eb275451671b781e22d953773dea5bf69f4e",
    "supported_models.py": "f007f32e815d375be891b7ce40c97b2153acaac9e8935cc709b4a28306690296",
    "plugin_utils.py": "31a0397e3b2a51f41e6799466bccf995b5fb01f9a20d8a8e343cad7ed27f7491",
}
OUTPUT_SHA256 = {
    "api/transformer.py": "d95f76c0788a0deec81c1b547a7a868c1b19f837cee9ccaff3e4cb9c2e5d727b",
    "api/differ.py": "6e6f789283cddba7a50a45c044e37748269793d39d45aa815e761b0758e97991",
    "api/supported_models.py": "a133de9de62242c46a32c1f02832d72b5a9127eeda1a74f22286aafd5516ccbe",
    "api/plugin_utils.py": "2c717a205e32259329d5b9e152a14ce5252b23bfdc67d3048c1291535a57146a",
    "api/front_port_compat.py": HELPER_SHA256,
}
# Each insertion uses one unique upstream anchor. The full-file hashes above are
# the actual compatibility guard; anchors make the small upstream diff readable.
REPLACEMENTS = {
    "transformer.py": [
        ("    supported_fields = _supported_diode_fields(object_type, supported_models)\n",
         "    from .front_port_compat import transform_legacy_mapping\n"
         "    transform_legacy_mapping(proto_json, object_type, node, nodes, supported_models, _transform_proto_json_1)\n\n"
         "    supported_fields = _supported_diode_fields(object_type, supported_models)\n"),
    ],
    "differ.py": [
        ("    # Cable terminations are model properties (reverse `terminations`\n",
         "    from .front_port_compat import active, mapping_snapshot\n"
         "    if object_type == \"dcim.frontport\" and active():\n"
         "        prechange_data[\"rear_ports\"] = mapping_snapshot(instance)\n\n"
         "    # Cable terminations are model properties (reverse `terminations`\n"),
    ],
    "supported_models.py": [
        ("    return netbox_get_serializer_for_model(model, prefix)\n",
         "    from .front_port_compat import serializer_for\n"
         "    return serializer_for(model, netbox_get_serializer_for_model(model, prefix))\n"),
    ],
    "plugin_utils.py": [
        ('            "positions",\n            "rear_port",\n',
         '            "positions",\n            "rear_ports",  # local qualification bridge\n            "rear_port",\n'),
    ],
}


def prepare_patch(plugin_root, helper):
    """Return every output only after all pinned inputs and Python syntax pass."""
    api = Path(plugin_root) / "api"
    if (api / "front_port_compat.py").exists():
        raise ValueError("Front-port compatibility helper already exists; use an unmodified pinned image.")
    outputs = {}
    for name, expected in SOURCE_SHA256.items():
        source = (api / name).read_bytes()
        if hashlib.sha256(source).hexdigest() != expected:
            raise ValueError(f"Pinned plugin source SHA256 mismatch: {name}")
        text = source.decode()
        for before, after in REPLACEMENTS[name]:
            if text.count(before) != 1:
                raise ValueError(f"Pinned plugin source anchor mismatch: {name}")
            text = text.replace(before, after)
        compile(text, name, "exec")
        outputs[api / name] = text.encode()
    helper = Path(helper).read_bytes()
    if hashlib.sha256(helper).hexdigest() != HELPER_SHA256:
        raise ValueError("Local compatibility helper SHA256 mismatch")
    compile(helper, "front_port_compat.py", "exec")
    outputs[api / "front_port_compat.py"] = helper
    actual = {str(path.relative_to(plugin_root)): hashlib.sha256(content).hexdigest()
              for path, content in outputs.items()}
    if actual != OUTPUT_SHA256:
        raise ValueError("Local compatibility patch output SHA256 mismatch")
    return outputs


def apply_patch(plugin_root, helper):
    outputs = prepare_patch(plugin_root, helper)
    # A failed Docker build is discarded. We do not patch a running container.
    for path, content in outputs.items():
        path.write_bytes(content)
    return {str(path.relative_to(plugin_root)): hashlib.sha256(content).hexdigest()
            for path, content in outputs.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", required=True, type=Path)
    parser.add_argument("--helper", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        apply_patch(args.plugin_root, args.helper)
    except (ValueError, OSError, SyntaxError) as error:
        parser.exit(1, f"Local compatibility patch refused: {error}\n")
    print(f"Applied {PATCH_ID}; local qualification only, NetBox {NETBOX_VERSION}, plugin {PLUGIN_VERSION}.")


if __name__ == "__main__":
    main()
