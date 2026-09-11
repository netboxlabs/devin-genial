"""Local qualification bridge; this is not an official Diode compatibility claim.

Installed inside netbox_diode_plugin.api by the hash-guarded build patch. Accept
the SDK's legacy one-position FrontPort mapping and let NetBox's own serializer
write PortMapping rows. Existing mappings are immutable unless identical; this
bridge deliberately does not implement general mapping replacement or deletion.

Native write/read contract:
https://github.com/netbox-community/netbox/blob/v4.7.0/netbox/dcim/api/serializers_/base.py
https://github.com/netbox-community/netbox/blob/v4.7.0/netbox/dcim/api/serializers_/device_components.py
"""


def active():
    from .compat import in_version_range
    return in_version_range("4.7.0", "4.7.0")


def transform_legacy_mapping(proto_json, object_type, node, nodes, supported, transform):
    """Reuse Diode's normal nested-reference resolver and dependency ordering."""
    if object_type != "dcim.frontport" or not active():
        return
    from rest_framework.exceptions import ValidationError
    from .common import UnresolvedReference

    if "rear_port" not in proto_json:
        if "rear_port_position" in proto_json:
            raise ValidationError({"rear_port_position": ["Supply rear_port with its position."]})
        return
    rear = proto_json.pop("rear_port")
    position = proto_json.pop("rear_port_position", 1)
    if not rear:
        raise ValidationError({"rear_port": ["The local bridge does not clear existing mappings."]})
    if proto_json.get("positions", 1) != 1:
        raise ValidationError({"positions": ["The local bridge supports one front position only."]})
    if not isinstance(position, int) or isinstance(position, bool) or position < 1:
        raise ValidationError({"rear_port_position": ["Expected a positive integer position."]})
    nested = transform(rear, "dcim.rearport", supported)
    reference = UnresolvedReference(object_type="dcim.rearport", uuid=nested[0]["_uuid"])
    node["rear_ports"] = [{"position": 1, "rear_port": reference, "rear_port_position": position}]
    node["_refs"].add(reference.uuid)
    nodes.extend(nested)


def mapping_snapshot(instance):
    """Use the native writable shape, not private mapping row IDs, for diffing."""
    return sorted(({
        "position": mapping.front_port_position,
        "rear_port": mapping.rear_port_id,
        "rear_port_position": mapping.rear_port_position,
    } for mapping in instance.mappings.all()), key=lambda item: item["position"])


def guard_mapping(*, mappings, device_id, positions, existing_positions=None, existing=()):
    """Validate the narrow write before the native serializer can change rows.

    `mappings` is native validated_data: the rear_port is a model instance and
    front_port_position is the serializer's source name. This function is plain
    Python so the destructive-boundary check also has a small offline test.
    """
    if positions != 1 or existing_positions not in (None, 1) or len(mappings) != 1:
        raise ValueError("The local bridge supports exactly one front position and one mapping.")
    mapping = mappings[0]
    front_position = mapping["front_port_position"]
    rear_position = mapping["rear_port_position"]
    rear = mapping["rear_port"]
    if front_position != 1 or isinstance(front_position, bool):
        raise ValueError("The local bridge supports front position 1 only.")
    if not isinstance(rear_position, int) or isinstance(rear_position, bool) or not 1 <= rear_position <= rear.positions:
        raise ValueError("Rear position is outside the rear port's available positions.")
    if rear.device_id != device_id:
        raise ValueError("Mapped front and rear ports must belong to the same device.")
    desired = [{"position": 1, "rear_port": rear.pk, "rear_port_position": rear_position}]
    if existing and list(existing) != desired:
        raise ValueError("The local bridge refuses to replace or remove an existing mapping.")


def serializer_for(model, native_serializer):
    """Keep the native serializer's create/update and mapping reconciliation."""
    if model._meta.label_lower != "dcim.frontport" or not active():
        return native_serializer
    from rest_framework.exceptions import ValidationError

    class LocalFrontPortSerializer(native_serializer):
        def validate(self, data):
            data = super().validate(data)
            if "mappings" not in data:
                return data
            instance = self.instance
            device = data.get("device") or (instance.device if instance else None)
            try:
                guard_mapping(
                    mappings=data["mappings"], device_id=device.pk if device else None,
                    positions=data.get("positions", instance.positions if instance else 1),
                    existing_positions=instance.positions if instance else None,
                    existing=mapping_snapshot(instance) if instance else (),
                )
            except (ValueError, KeyError) as error:
                raise ValidationError({"rear_ports": [str(error)]}) from None
            return data

    return LocalFrontPortSerializer
