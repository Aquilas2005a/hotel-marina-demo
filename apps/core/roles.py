from django.contrib.auth.models import Group, Permission


ROLE_MODEL_ACTIONS = {
    "Gestionnaire hôtelier": {
        "HotelProfile": {"view", "change"},
        "Room": {"view", "add", "change"},
        "RoomUnit": {"view", "add", "change"},
        "RoomBlock": {"view", "add", "change"},
        "RoomRate": {"view", "add", "change"},
        "RoomFee": {"view", "add", "change"},
        "PromotionCode": {"view", "add", "change"},
        "Reservation": {"view", "add", "change"},
        "CustomerProfile": {"view", "add", "change"},
        "MenuItem": {"view", "add", "change"},
        "MenuOption": {"view", "add", "change"},
        "RestaurantTable": {"view", "add", "change"},
        "RestaurantOrder": {"view", "add", "change"},
        "RestaurantOrderLine": {"view", "add", "change"},
        "HotelInterior": {"view", "add", "change"},
        "HotelAmenity": {"view", "add", "change"},
        "Testimonial": {"view", "add", "change"},
        "Invoice": {"view"},
        "RefundRecord": {"view"},
        "RestaurantStockMovement": {"view"},
        "BusinessAuditLog": {"view"},
    },
    "Réceptionniste": {
        "Room": {"view"},
        "RoomUnit": {"view"},
        "RoomBlock": {"view"},
        "RoomRate": {"view"},
        "Reservation": {"view", "add", "change"},
        "CustomerProfile": {"view", "add", "change"},
        "Invoice": {"view"},
        "RefundRecord": {"view"},
    },
    "Équipe restaurant": {
        "MenuItem": {"view", "change"},
        "RestaurantStockMovement": {"view"},
        "MenuOption": {"view", "add", "change"},
        "RestaurantTable": {"view", "change"},
        "RestaurantOrder": {"view", "add", "change"},
        "RestaurantOrderLine": {"view", "add", "change"},
    },
}


def provision_staff_roles():
    """Crée ou actualise les groupes de travail et leurs permissions Django."""
    permissions = Permission.objects.filter(content_type__app_label="core")
    by_codename = {permission.codename: permission for permission in permissions}
    groups = []
    for group_name, model_actions in ROLE_MODEL_ACTIONS.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        role_permissions = []
        for model_name, actions in model_actions.items():
            for action in actions:
                permission = by_codename.get(f"{action}_{model_name.lower()}")
                if permission is not None:
                    role_permissions.append(permission)
        group.permissions.set(role_permissions)
        groups.append(group)
    return groups
