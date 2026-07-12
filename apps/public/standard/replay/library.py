"""replay — RecipeLibrary, the vault-backed collection of distilled recipes.

A recipe is a ``tag: recipe`` note under ``30_Resources/EmptyOS/recipes/``. The
machine-readable steps/inputs/verify live in ``*_json`` frontmatter (decoded by
``shared.parse_recipe``); the body is a human-readable mirror.
"""

from __future__ import annotations

from emptyos.sdk.vault_library import VaultLibrary


class RecipeLibrary(VaultLibrary):
    # "replay", NOT "recipe" — "recipe" collides with the user's cooking-recipe
    # notes (nutrition/meal-planner). A saved workflow artifact is a "replay".
    tag = "replay"
    fields = {
        "id": str,
        "name": str,
        "goal": str,
        "when_to_use": str,
        "status": str,
        "author": str,
        "source_session": str,
        "source_trace": str,
        "created": str,
        "updated": str,
        "steps_count": int,  # machine spec lives in the body json fence, not frontmatter
    }
    sort_key = "updated"
    sort_reverse = True
    fallback_folder = "30_Resources/EmptyOS/replays"
