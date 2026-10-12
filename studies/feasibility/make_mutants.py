"""Seed bugs into forge's checkpoint 8 reference solution, one per specification property.

Each mutant is a copy of the solution with exact text replacements, written to
data/forge-mutants/<name>/. They are the kind of slip an agent makes when it edits
nearby code: a dropped guard, a write before validation, a check moved or removed.
"""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "scb-problems" / "forge" / "solutions" / "checkpoint_8"
OUT = ROOT / "data" / "forge-mutants"

MUTANTS = {
    # Bind no longer refuses an assignment that already has bound allocations.
    "bind-no-duplicate-guard": [(
        '                return _emit_error("duplicate_assignment", assignment_id)\n',
        "                pass\n",
    )],
    # Bind applies the operations on known allocations before rejecting unknown ones.
    "bind-not-atomic": [(
        '    if missing:\n        return _emit_error("not_found", ", ".join(missing))\n\n    has_add',
        '    if missing:\n'
        '        for allocation_uuid, operation in operations.items():\n'
        '            if str(allocation_uuid) in allocation_map and operation == "add":\n'
        '                allocation_map[str(allocation_uuid)]["binding_status"] = "bound"\n'
        '                allocation_map[str(allocation_uuid)]["assignment_id"] = assignment_id\n'
        '        _save_allocations(data_dir, allocations)\n'
        '        return _emit_error("not_found", ", ".join(missing))\n\n    has_add',
    )],
    # Remove marks the allocation unbound but keeps its assignment.
    "remove-keeps-assignment": [(
        '            allocation["binding_status"] = "unbound"\n            allocation["assignment_id"] = None\n',
        '            allocation["binding_status"] = "unbound"\n',
    )],
    # TYPE_A requirement sets no longer double.
    "typea-not-doubled": [(
        '    if any(capability == "category=TYPE_A" for capability in capabilities):\n        return 2\n',
        "",
    )],
    # Tag create no longer refuses a duplicate key on the same module.
    "tag-duplicate-allowed": [(
        '            return _emit_error("conflict", f"{module_uuid}, {key}")\n',
        "            pass\n",
    )],
    # Module create forgets to record the module on its unit.
    "module-not-linked": [(
        '    units[parent_index] = _normalize_unit_for_storage(parent_unit)\n    _save_units(data_dir, units)\n',
        "",
    )],
    # Delete by names removes the names it can resolve before reporting the rest.
    "delete-names-partial": [(
        '        if unresolved:\n            return _emit_error("not_found", ", ".join(unresolved))\n\n        name_set',
        '        if unresolved:\n'
        '            _save_blueprints(data_dir, [b for b in blueprints if b.get("name") not in set(requested_names)])\n'
        '            return _emit_error("not_found", ", ".join(unresolved))\n\n        name_set',
    )],
    # The system-scope check on protected operations is dropped.
    "system-scope-allowed": [(
        '    if CALLER_SCOPE == "system":\n        return _emit_error(\n            "forbidden",\n'
        '            f"system scope is not supported for {operation_id}",\n        )\n',
        "",
    )],
    # Flash accepts any well-formed image.
    "flash-any-image": [(
        '    if image not in known_images:\n        return _emit_error("flash_failed", image)\n',
        "",
    )],
    # tenant_id is shown at every revision.
    "tenant-always-visible": [(
        '    if _revision_at_least((1, 1)):\n        serialized["tenant_id"] = serialized.get("tenant_id")\n'
        '    elif "tenant_id" in serialized:\n        serialized.pop("tenant_id")\n',
        '    serialized["tenant_id"] = serialized.get("tenant_id")\n',
    )],
}


def main() -> None:
    for name, edits in MUTANTS.items():
        dest = OUT / name
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(SOURCE, dest)
        src = (dest / "forge.py").read_text()
        for old, new in edits:
            assert src.count(old) == 1, f"{name}: pattern found {src.count(old)} times"
            src = src.replace(old, new)
        (dest / "forge.py").write_text(src)
        print(f"wrote {dest}")


if __name__ == "__main__":
    main()
