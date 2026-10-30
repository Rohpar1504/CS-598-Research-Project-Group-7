"""Adapter for SlopCodeBench's forge app (the model is lean/Forge/Model.lean).

It drives the real program through its command line only:
- `run(action)` turns an abstract action into a command (concretization), runs it, and
  translates the response into the model's `Out` (abstraction);
- `observe()` reads the whole state through forge's own read commands into the model's `Obs`;
- `snapshot`/`restore`/`release` let trace validation branch from a shared prefix;
- `describe(action)` gives the command an action runs, for reproductions.

Abstract values: each resource type has its own ids, the k-th created resource of a type
being id k. An id that was never created maps to a well-formed UUID that does not exist, with
a per-type prefix so that it maps back. Names and values are naturals mapped to strings such
as `bp-3` or `cap-1`; in requirement sets, capability 0 is `category=TYPE_A`. Anything the
model cannot express becomes `inexpressible(...)`.
"""

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from companion import runner
from companion.abstraction import inexpressible

KINDS = ("blueprint", "allocation", "unit", "module", "tag")
IMAGE_PREFIX = "f0000000-0000-4000-8000-"
INVALID_IMAGE = "NOT-AN-IMAGE"
REVISIONS = {"r10": "1.0", "r11": "1.1", "r12": "1.2", "latest": "latest", "unparseable": "1.x", "unsupported": "2.0"}
SCOPES = {"project": "project", "system": "system", "invalid": "galaxy"}
RULES = "blueprint:create: admin\nblueprint:delete: admin\n"

# Invalid payloads, as (path into the valid payload, value to put there).
BAD_BLUEPRINT = {
    "emptyName": (("name",), ""),
    "noSets": (("requirement_sets",), []),
    "zeroCount": (("requirement_sets", 0, "resource_count"), 0),
    "boolCount": (("requirement_sets", 0, "resource_count"), True),
    "unsupportedKey": (("requirement_sets", 0, "color"), "red"),
    "capsNotList": (("requirement_sets", 0, "capabilities"), "cap-1"),
}
BAD_UNIT = {
    "emptyCategory": (("category",), ""),
    "negativeCapacity": (("capacity",), -1),
    "boolCapacity": (("capacity",), True),
    "systemField": (("status",), "active"),
    "capsNotList": (("capabilities",), "cap-0"),
    "emptyParent": (("parent_uuid",), ""),
}


def ctor(value) -> tuple[str, dict]:
    """The constructor name and arguments of a Lean-derived JSON value."""
    if isinstance(value, str):
        return value, {}
    (name, args), = value.items()
    return name, args


def with_value(payload: dict, path: tuple, value) -> dict:
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return payload


def by_key(*fields):
    """Sort key over abstract views; inexpressible values sort last."""
    return lambda v: tuple((not isinstance(v[f], int), v[f] if isinstance(v[f], int) else 0) for f in fields)


class ForgeAdapter:
    def __init__(self, program: Path):
        self.program = Path(program)
        # Data dirs and snapshots go under COMPANION_TMP if set: /tmp is often in RAM.
        root = os.environ.get("COMPANION_TMP")
        if root:
            Path(root).mkdir(parents=True, exist_ok=True)
        self.workdir = Path(tempfile.mkdtemp(prefix="forge-", dir=root))
        self.rules = self.workdir / "rules.yaml"
        self.rules.write_text(RULES)
        self.data_dir = self.workdir / "data"
        self._snapshots = 0
        self.reset()

    def reset(self) -> None:
        shutil.rmtree(self.data_dir, ignore_errors=True)
        self.ids = {kind: [] for kind in KINDS}

    def close(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def snapshot(self):
        self._snapshots += 1
        path = self.workdir / f"snap-{self._snapshots}"
        if self.data_dir.exists():
            shutil.copytree(self.data_dir, path)
        return path, {k: list(v) for k, v in self.ids.items()}

    def restore(self, snap) -> None:
        path, ids = snap
        shutil.rmtree(self.data_dir, ignore_errors=True)
        if path.exists():
            shutil.copytree(path, self.data_dir)
        self.ids = {k: list(v) for k, v in ids.items()}

    def release(self, snap) -> None:
        shutil.rmtree(snap[0], ignore_errors=True)

    # The interface ------------------------------------------------------------------------

    def run(self, action) -> dict:
        name, args = ctor(action["cmd"])
        argv, payload = getattr(self, f"cmd_{name}")(args)
        code, resp = self.call(self.flags(action["ctx"]) + argv, payload)
        self.register(name, resp)
        revision = resp.get("revision") if isinstance(resp, dict) else None
        return {"body": self.body(name, code, resp), "revision": revision}

    def observe(self) -> dict:
        lists = {"blueprints": (["blueprint", "list"], self.bp_view),
                 "allocations": (["--revision", "latest", "allocation", "list"], self.alloc_view),
                 "units": (["unit", "list"], self.unit_view),
                 "modules": (["module", "list"], self.module_view),
                 "tags": (["tag", "list"], self.tag_view)}
        obs = {}
        for name, (argv, view) in lists.items():
            code, resp = self.call(argv, None)
            if code != 0 or not isinstance(resp, dict) or name not in resp:
                return inexpressible(f"the state ({' '.join(argv)} failed)", resp)
            obs[name] = [view(x) for x in resp[name]]
        code, ledger = self.call(["ledger", "capabilities"], None)
        if code != 0 or not isinstance(ledger, dict) or "capabilities" not in ledger:
            return inexpressible("the state (ledger capabilities failed)", ledger)
        code, schema = self.call(["schema", "version"], None)
        for name in ("blueprints", "allocations", "units", "modules"):
            obs[name].sort(key=by_key("id"))
        obs["tags"].sort(key=by_key("module", "key", "id"))
        obs["ledger"] = [self.num(c, "cap-") for c in ledger["capabilities"]]
        obs["schema"] = schema.get("version") if code == 0 and isinstance(schema, dict) else None
        return obs

    def describe(self, action) -> str:
        """The command an action runs, with known UUIDs shown as `<kind k>`. Call it after
        the trace has run, so that created ids are known."""
        name, args = ctor(action["cmd"])
        argv, payload = getattr(self, f"cmd_{name}")(args)
        text = " ".join(["forge", *self.flags(action["ctx"]), *argv]).replace(str(self.rules), "rules.yaml")
        if payload is not None:
            text += " <<< " + json.dumps(payload)
        for kind in KINDS:
            for k, uuid in enumerate(self.ids[kind]):
                text = text.replace(uuid, f"<{kind} {k}>")
            text = re.sub(re.escape(self.placeholder(kind)) + r"0*(\d+)",
                          lambda m: f"<{kind} {m.group(1)}, never created>", text)
        return text

    def call(self, argv: list, payload: dict | None):
        result = runner.run(self.program, ["--data-dir", self.data_dir, *argv],
                            None if payload is None else json.dumps(payload))
        try:
            return result.code, json.loads(result.stdout)
        except json.JSONDecodeError:
            return result.code, {"unparseable": result.stdout[:200], "stderr": result.stderr[-300:]}

    # Abstract ids ------------------------------------------------------------------------

    @staticmethod
    def placeholder(kind: str) -> str:
        return f"00000000-0000-4000-80{KINDS.index(kind) + 1:02d}-"

    def uuid(self, kind: str, k: int) -> str:
        ids = self.ids[kind]
        return ids[k] if k < len(ids) else f"{self.placeholder(kind)}{k:012d}"

    def id(self, kind: str, uuid):
        ids = self.ids[kind]
        if uuid in ids:
            return ids.index(uuid)
        p = self.placeholder(kind)
        if isinstance(uuid, str) and uuid.startswith(p) and uuid[len(p):].isdigit():
            return int(uuid[len(p):])
        if isinstance(uuid, str):          # appeared without a create we saw: next id in order
            ids.append(uuid)
            return len(ids) - 1
        return inexpressible(f"a {kind} id", uuid)

    def register(self, name: str, resp) -> None:
        """Record the UUIDs a successful create returned, in order."""
        if not isinstance(resp, dict) or "error" in resp:
            return
        created = {"bpCreate": ("blueprint", [resp]), "unitCreate": ("unit", [resp]),
                   "modCreate": ("module", [resp]), "tagCreate": ("tag", [resp]),
                   "alCreate": ("allocation", resp.get("allocations", []))}.get(name)
        if created:
            kind, items = created
            for item in items:
                if item.get("uuid") and item["uuid"] not in self.ids[kind]:
                    self.ids[kind].append(item["uuid"])

    # Concretization: one method per command --------------------------------------------

    def flags(self, ctx: dict) -> list:
        out = []
        if ctx["rev"] != "omitted":
            out += ["--revision", REVISIONS[ctx["rev"]]]
        if ctx["scope"] != "omitted":
            out += ["--scope", SCOPES[ctx["scope"]]]
        if ctx["rules"]:
            out += ["--rules-file", str(self.rules)]
        if ctx["role"] is not None:
            out += ["--role", ctx["role"]]
        return out

    @staticmethod
    def image(i: int) -> str:
        return f"{IMAGE_PREFIX}{i:012d}"

    @staticmethod
    def req_set(r: dict) -> dict:
        out = {"resource_type": f"rt-{r['rtype']}", "resource_count": r["count"]}
        if r["caps"] is not None:
            out["capabilities"] = ["category=TYPE_A" if c == 0 else f"cap-{c}" for c in r["caps"]]
        return out

    def cmd_version(self, a):
        return ["version"], None

    def cmd_bpCreate(self, a):
        return ["blueprint", "create"], {"name": f"bp-{a['name']}", "requirement_sets": [self.req_set(r) for r in a["sets"]]}

    def cmd_bpCreateBad(self, a):
        valid = {"name": "bp-8", "requirement_sets": [{"resource_type": "rt-0", "resource_count": 1}]}
        return ["blueprint", "create"], with_value(valid, *BAD_BLUEPRINT[a["bad"]])

    def cmd_bpList(self, a):
        return ["blueprint", "list"], None

    def cmd_bpGet(self, a):
        return ["blueprint", "get", self.uuid("blueprint", a["id"])], None

    def cmd_bpGetByName(self, a):
        return ["blueprint", "get", f"bp-{a['name']}"], None

    def cmd_bpDelete(self, a):
        return ["blueprint", "delete", self.uuid("blueprint", a["id"])], None

    def cmd_bpDeleteNames(self, a):
        names = [f"bp-{args['n']}" if kind == "name" else "" for kind, args in map(ctor, a["names"])]
        return ["blueprint", "delete", "--names", ",".join(names)], None

    def cmd_alCreate(self, a):
        return ["allocation", "create"], {"blueprint_name": f"bp-{a['blueprint']}"}

    def cmd_alCreateEmptyName(self, a):
        return ["allocation", "create"], {"blueprint_name": ""}

    def cmd_alList(self, a):
        argv = ["allocation", "list"]
        if a["assignment"] is not None:
            argv += ["--assignment", f"as-{a['assignment']}"]
        if a["bound"] is not None:
            argv += ["--status", "bound" if a["bound"] else "unbound"]
        return argv, None

    def cmd_alGet(self, a):
        return ["allocation", "get", self.uuid("allocation", a["id"])], None

    def cmd_alBind(self, a):
        payload = {"assignment_id": f"as-{a['assignment']}",
                   "allocations": {self.uuid("allocation", i): ("nonsense" if op == "bogus" else op) for i, op in a["ops"]}}
        kind, args = ctor(a["tenant"])
        tenant = {"null": None, "value": f"tn-{args.get('t')}", "notString": 5}
        if kind in tenant:
            payload["tenant_id"] = tenant[kind]
        return ["allocation", "bind"], payload

    def cmd_alBindBad(self, a):
        if a["bad"] == "noAssignment":
            return ["allocation", "bind"], {"allocations": {self.uuid("allocation", 0): "add"}}
        return ["allocation", "bind"], {"assignment_id": "as-0", "allocations": {}}

    def cmd_alDeleteAssignment(self, a):
        return ["allocation", "delete", "--assignment", f"as-{a['assignment']}"], None

    def cmd_alDeleteIds(self, a):
        ids = [self.uuid("allocation", args["k"]) if kind == "id" else "" for kind, args in map(ctor, a["ids"])]
        return ["allocation", "delete", "--ids", ",".join(ids)], None

    def cmd_alDeleteNeither(self, a):
        return ["allocation", "delete"], None

    def cmd_unitCreate(self, a):
        payload = {"category": f"cat-{a['category']}", "manufacturer": "acme", "host": f"host-{a['host']}",
                   "capacity": a["capacity"]}
        if a["caps"]:
            payload["capabilities"] = [f"cap-{c}" for c in a["caps"]]
        if a["parent"] is not None:
            payload["parent_uuid"] = self.uuid("unit", a["parent"])
        return ["unit", "create"], payload

    def cmd_unitCreateBad(self, a):
        valid = {"category": "cat-0", "manufacturer": "acme", "host": "host-0", "capacity": 1}
        return ["unit", "create"], with_value(valid, *BAD_UNIT[a["bad"]])

    def cmd_unitActivate(self, a):
        return ["unit", "activate", self.uuid("unit", a["id"])], None

    def cmd_unitDeactivate(self, a):
        return ["unit", "deactivate", self.uuid("unit", a["id"])], None

    def cmd_unitGet(self, a):
        return ["unit", "get", self.uuid("unit", a["id"])], None

    def cmd_unitList(self, a):
        kind, f = ctor(a["filter"])
        extra = {"all": lambda: [],
                 "category": lambda: ["--category", f"cat-{f['c']}"],
                 "host": lambda: ["--host", f"host-{f['h']}"],
                 "status": lambda: ["--filter", "status=" + ("active" if f["active"] else "inactive")],
                 "capacity": lambda: ["--filter", f"capacity={f['n']}"],
                 "parentNull": lambda: ["--filter", "parent_uuid=null"],
                 "unknownField": lambda: ["--filter", "nosuchfield=x"],
                 "malformed": lambda: ["--filter", "status"]}[kind]()
        return ["unit", "list", *extra], None

    def cmd_ledger(self, a):
        return ["ledger", "capabilities"], None

    def cmd_modCreate(self, a):
        payload = {"unit_uuid": self.uuid("unit", a["unit"]), "component_type": f"ct-{a['ctype']}"}
        kind, args = ctor(a["firmware"])
        firmware = {"null": None, "image": self.image(args.get("i", 0)), "invalid": INVALID_IMAGE}
        if kind in firmware:
            payload["firmware_image"] = firmware[kind]
        return ["module", "create"], payload

    def cmd_modCreateEmptyType(self, a):
        return ["module", "create"], {"unit_uuid": self.uuid("unit", a["unit"]), "component_type": ""}

    def cmd_modList(self, a):
        kind, f = ctor(a["filter"])
        extra = {"all": lambda: [],
                 "unit": lambda: ["--unit", self.uuid("unit", f["u"])],
                 "ctype": lambda: ["--filter", f"component_type=ct-{f['c']}"],
                 "firmwareNull": lambda: ["--filter", "firmware_image=null"],
                 "unknownField": lambda: ["--filter", "nosuchfield=x"],
                 "malformed": lambda: ["--filter", "component_type"]}[kind]()
        return ["module", "list", *extra], None

    def cmd_modGet(self, a):
        return ["module", "get", self.uuid("module", a["id"])], None

    def cmd_modFlash(self, a):
        kind, args = ctor(a["image"])
        image = self.image(args["i"]) if kind == "image" else INVALID_IMAGE
        return ["module", "flash", self.uuid("module", a["id"]), "--image", image], None

    def cmd_tagCreate(self, a):
        return ["tag", "create"], {"module_uuid": self.uuid("module", a["module"]), "key": f"key-{a['key']}",
                                   "value": f"val-{a['value']}"}

    def cmd_tagCreateEmptyKey(self, a):
        return ["tag", "create"], {"module_uuid": self.uuid("module", a["module"]), "key": "", "value": "val-0"}

    def cmd_tagList(self, a):
        argv = ["tag", "list"]
        if a["module"] is not None:
            argv += ["--module", self.uuid("module", a["module"])]
        if a["key"] is not None:
            argv += ["--key", f"key-{a['key']}"]
        return argv, None

    def cmd_tagGet(self, a):
        return ["tag", "get", self.uuid("tag", a["id"])], None

    def cmd_tagDelete(self, a):
        return ["tag", "delete", self.uuid("tag", a["id"])], None

    def cmd_tagUpdate(self, a):
        return ["tag", "update", "x"], None

    def cmd_schemaInit(self, a):
        return ["schema", "init"], None

    def cmd_schemaVersion(self, a):
        return ["schema", "version"], None

    def cmd_schemaUpgrade(self, a):
        return ["schema", "upgrade"], None

    # Abstraction of responses --------------------------------------------------------------

    @staticmethod
    def num(value, prefix: str):
        if isinstance(value, str) and value.startswith(prefix) and value[len(prefix):].isdigit():
            return int(value[len(prefix):])
        return inexpressible(f"a value of the form {prefix}N", value)

    def body(self, name: str, code: int, resp) -> dict:
        """The model's `Body` for a command's response."""
        if isinstance(resp, dict) and "error" in resp:
            error = {"error": {"category": resp["error"]}}
            return error if code == 1 else inexpressible(f"exit code 1 with an error (got {code})", resp)
        if code != 0:
            return inexpressible(f"exit code 0 (got {code})", resp)
        if not isinstance(resp, dict):
            return inexpressible("a JSON object", resp)
        r = {k: v for k, v in resp.items() if k != "revision"}
        reader = self.READERS.get(name)
        try:
            return reader(self, r) if reader else inexpressible(f"a response to {name}", r)
        except (KeyError, TypeError, AttributeError):
            return inexpressible(f"a response to {name}", r)

    def read_version(self, r):
        return "versionInfo" if r == {"max_revision": "1.2", "min_revision": "1.0"} else inexpressible("version info", r)

    def read_blueprint(self, r):
        return {"blueprint": {"b": self.bp_view(r)}}

    def read_blueprints(self, r):
        return {"blueprints": {"bs": self.in_list_order(r["blueprints"], ("created_at",), self.bp_view)}}

    def read_deleted(self, r):
        return {"deleted": {"n": r["deleted"]}}

    def read_new_allocations(self, r):
        return {"allocations": {"as": [self.alloc_view(a) for a in r["allocations"]]}}

    def read_allocations(self, r):
        return {"allocations": {"as": self.in_list_order(r["allocations"], ("created_at",), self.alloc_view)}}

    def read_allocation(self, r):
        return {"allocation": {"a": self.alloc_view(r)}}

    def read_accepted(self, r):
        return "accepted" if r == {"status": "accepted"} else inexpressible("status accepted", r)

    def read_unit(self, r):
        return {"unit": {"u": self.unit_view(r)}}

    def read_units(self, r):
        return {"units": {"us": self.in_list_order(r["units"], ("host", "category", "created_at"), self.unit_view)}}

    def read_ledger(self, r):
        return {"caps": {"cs": [self.num(c, "cap-") for c in r["capabilities"]]}}

    def read_module(self, r):
        return {"module": {"m": self.module_view(r)}}

    def read_modules(self, r):
        # Ordered by parent unit UUID, which is random: compare in (unit, creation) order.
        return {"modules": {"ms": sorted(map(self.module_view, r["modules"]), key=by_key("unit", "id"))}}

    def read_tag(self, r):
        return {"tag": {"t": self.tag_view(r)}}

    def read_tags(self, r):
        # Ordered by module UUID (random), then key: compare in (module, key, creation) order.
        return {"tags": {"ts": sorted(map(self.tag_view, r["tags"]), key=by_key("module", "key", "id"))}}

    def read_schema_status(self, r):
        return {"schemaStatus": {"status": r.get("status"), "version": r.get("version")}}

    def read_schema_version(self, r):
        return {"schemaVersion": {"version": r["version"]}}

    READERS = {
        "version": read_version,
        "bpCreate": read_blueprint, "bpGet": read_blueprint, "bpGetByName": read_blueprint,
        "bpList": read_blueprints,
        "bpDelete": read_deleted, "bpDeleteNames": read_deleted, "alDeleteAssignment": read_deleted,
        "alDeleteIds": read_deleted, "tagDelete": read_deleted,
        "alCreate": read_new_allocations, "alList": read_allocations, "alGet": read_allocation,
        "alBind": read_accepted,
        "unitCreate": read_unit, "unitActivate": read_unit, "unitDeactivate": read_unit, "unitGet": read_unit,
        "unitList": read_units, "ledger": read_ledger,
        "modCreate": read_module, "modGet": read_module, "modFlash": read_module, "modList": read_modules,
        "tagCreate": read_tag, "tagGet": read_tag, "tagList": read_tags,
        "schemaInit": read_schema_status, "schemaUpgrade": read_schema_status, "schemaVersion": read_schema_version,
    }

    # Views of single resources --------------------------------------------------------------

    def bp_view(self, b: dict) -> dict:
        sets = [{"rtype": self.num(r.get("resource_type"), "rt-"), "count": r.get("resource_count"),
                 "caps": [0 if c == "category=TYPE_A" else self.num(c, "cap-") for c in r["capabilities"]]
                         if "capabilities" in r else None}
                for r in b.get("requirement_sets", [])]
        return {"id": self.id("blueprint", b.get("uuid")), "name": self.num(b.get("name"), "bp-"), "sets": sets}

    def alloc_view(self, a: dict) -> dict:
        if "tenant_id" not in a:
            tenant = "hidden"
        elif a["tenant_id"] is None:
            tenant = "null"
        else:
            tenant = {"value": {"t": self.num(a["tenant_id"], "tn-")}}
        return {"id": self.id("allocation", a.get("uuid")), "blueprint": self.num(a.get("blueprint_name"), "bp-"),
                "setIndex": a.get("requirement_set_index"),
                "assignment": None if a.get("assignment_id") is None else self.num(a["assignment_id"], "as-"),
                "status": a.get("binding_status"), "tenant": tenant}

    def unit_view(self, u: dict) -> dict:
        modules = [self.id("module", m) for m in u.get("module_uuids", [])]
        return {"id": self.id("unit", u.get("uuid")), "category": self.num(u.get("category"), "cat-"),
                "host": self.num(u.get("host"), "host-"), "capacity": u.get("capacity"),
                "parent": None if u.get("parent_uuid") is None else self.id("unit", u["parent_uuid"]),
                "caps": [self.num(c, "cap-") for c in u.get("capabilities", [])], "status": u.get("status"),
                "reservedCapacity": u.get("reserved_capacity"),
                # module_uuids are sorted by UUID, which is random; the model keeps creation order.
                "modules": sorted(modules, key=lambda m: (not isinstance(m, int), m if isinstance(m, int) else 0))}

    def module_view(self, m: dict) -> dict:
        firmware = m.get("firmware_image")
        return {"id": self.id("module", m.get("uuid")), "unit": self.id("unit", m.get("unit_uuid")),
                "ctype": self.num(m.get("component_type"), "ct-"),
                "firmware": None if firmware is None else self.num(firmware, IMAGE_PREFIX)}

    def tag_view(self, t: dict) -> dict:
        return {"id": self.id("tag", t.get("uuid")), "module": self.id("module", t.get("module_uuid")),
                "key": self.num(t.get("key"), "key-"), "value": self.num(t.get("value"), "val-")}

    def in_list_order(self, items: list, tie_fields: tuple, view) -> list:
        """Views in the program's order, except that within runs of equal `tie_fields` (such as
        the same created_at second) they are put in creation order: the program breaks those
        ties by random UUID, which the model cannot predict."""
        out, i = [], 0
        while i < len(items):
            j = i
            key = tuple(items[i].get(f) for f in tie_fields)
            while j < len(items) and tuple(items[j].get(f) for f in tie_fields) == key:
                j += 1
            out += sorted(map(view, items[i:j]), key=by_key("id"))
            i = j
        return out
