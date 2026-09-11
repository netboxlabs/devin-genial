"""Small public profile dispatcher; profiles compose shared construction blocks."""

from .model import resolve_recipe


def generate(recipe, previous=None):
    recipe = resolve_recipe(recipe)
    if recipe["profile"] == "enterprise-data-center":
        from .enterprise import generate as build
    elif recipe["profile"] == "school-district":
        from .school import generate as build
    elif recipe["profile"] == "hospital-clinics":
        from .hospital import generate as build
    elif recipe["profile"] == "provider-backbone":
        from .provider import generate as build
    else:
        from .bank import generate as build
    return build(recipe, previous=previous)
