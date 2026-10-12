"""Adapter for the whole forge app (see lean/Forge/Model.lean for the abstraction).

It drives the real program through its command line only. `run` turns an abstract action
into a command (concretization) and the response into the model's terms (abstraction);
`observe` reads all state through forge's own read commands.

Each resource type has its own abstract ids: the k-th created resource of that type is id k.
An id that was never created maps to a well-formed UUID that does not exist, with a
per-type prefix so that it can be mapped back.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KINDS = ("blueprint", "allocation", "unit", "module", "tag")
IMAGE_PREFIX = "f0000000-0000-4000-8000-"
REVISIONS = {"r10": "1.0", "r11": "1.1", "r12": "1.2", "latest": "latest",
             "unparseable": "1.x", "unsupported": "2.0"}
SCOPES = {"project": "project", "system": "system", "invalid": "galaxy"}


def _one(v):
    """The (constructor, arguments) of a Lean-derived JSON value."""
    if isinstance(v, str):
        return v, {}
    (k, args), = v.items()
    return k, args


class ForgeAdapter:
    def __init__(self, program: Path):
        self.program = Path(program)
        self.workdir = Path(tempfile.mkdtemp(prefix="forge-"))
        self.rules = self.workdir / "rules.yaml"
        self.rules.write_text("blueprint:create: admin\nblueprint:delete: admin\n")
        self.reset()

    def reset(self) -> None:
        self.data_dir = self.workdir / "data"
        shutil.rmtree(self.data_dir, ignore_errors=True)
        self.ids = {k: [] for k in KINDS}

    def close(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    # Ids -------------------------------------------------------------------------------

    def _placeholder(self, kind: str) -> str:
        return f"00000000-0000-4000-80{KINDS.index(kind) + 1:02d}-"

    def uuid(self, kind: str, k: int) -> str:
        ids = self.ids[kind]
        return ids[k] if k < len(ids) else f"{self._placeholder(kind)}{k:012d}"

    def id(self, kind: str, uuid):
        ids = self.ids[kind]
        if uuid in ids:
            return ids.index(uuid)
        p = self._placeholder(kind)
        if isinstance(uuid, str) and uuid.startswith(p) and uuid[len(p):].isdigit():
            return int(uuid[len(p):])
        if isinstance(uuid, str):          # appeared without a create we saw
            ids.append(uuid)
            return len(ids) - 1
        return f"unknown:{uuid}"

    # Concretization --------------------------------------------------------------------

    @staticmethod
    def image(i: int) -> str:
        return f"{IMAGE_PREFIX}{i:012d}"

    @staticmethod
    def req_cap(c: int) -> str:
        return "category=TYPE_A" if c == 0 else f"cap-{c}"

    def req_set(self, r: dict) -> dict:
        out = {"resource_type": f"rt-{r['rtype']}", "resource_count": r["count"]}
        if r["caps"] is not None:
            out["capabilities"] = [self.req_cap(c) for c in r["caps"]]
        return out

    def flags(self, ctx: dict) -> list[str]:
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

    def command(self, cmd) -> tuple[list[str], dict | None]:
        k, a = _one(cmd)
        U = self.uuid
        if k == "version":
            return ["version"], None
        if k == "bpCreate":
            return ["blueprint", "create"], {"name": f"bp-{a['name']}", "requirement_sets": [self.req_set(r) for r in a["sets"]]}
        if k == "bpCreateBad":
            p = {"name": "bp-8", "requirement_sets": [{"resource_type": "rt-0", "resource_count": 1}]}
            bad = a["bad"]
            if bad == "emptyName": p["name"] = ""
            elif bad == "noSets": p["requirement_sets"] = []
            elif bad == "zeroCount": p["requirement_sets"][0]["resource_count"] = 0
            elif bad == "boolCount": p["requirement_sets"][0]["resource_count"] = True
            elif bad == "unsupportedKey": p["requirement_sets"][0]["color"] = "red"
            elif bad == "capsNotList": p["requirement_sets"][0]["capabilities"] = "cap-1"
            return ["blueprint", "create"], p
        if k == "bpList":
            return ["blueprint", "list"], None
        if k == "bpGet":
            return ["blueprint", "get", U("blueprint", a["id"])], None
        if k == "bpGetByName":
            return ["blueprint", "get", f"bp-{a['name']}"], None
        if k == "bpDelete":
            return ["blueprint", "delete", U("blueprint", a["id"])], None
        if k == "bpDeleteNames":
            names = [f"bp-{_one(n)[1]['n']}" if _one(n)[0] == "name" else "" for n in a["names"]]
            return ["blueprint", "delete", "--names", ",".join(names)], None
        if k == "alCreate":
            return ["allocation", "create"], {"blueprint_name": f"bp-{a['blueprint']}"}
        if k == "alCreateEmptyName":
            return ["allocation", "create"], {"blueprint_name": ""}
        if k == "alList":
            args = ["allocation", "list"]
            if a["assignment"] is not None:
                args += ["--assignment", f"as-{a['assignment']}"]
            if a["bound"] is not None:
                args += ["--status", "bound" if a["bound"] else "unbound"]
            return args, None
        if k == "alGet":
            return ["allocation", "get", U("allocation", a["id"])], None
        if k == "alBind":
            p = {"assignment_id": f"as-{a['assignment']}",
                 "allocations": {U("allocation", i): ("nonsense" if op == "bogus" else op) for i, op in a["ops"]}}
            tk, ta = _one(a["tenant"])
            if tk == "null": p["tenant_id"] = None
            elif tk == "value": p["tenant_id"] = f"tn-{ta['t']}"
            elif tk == "notString": p["tenant_id"] = 5
            return ["allocation", "bind"], p
        if k == "alBindBad":
            if a["bad"] == "noAssignment":
                return ["allocation", "bind"], {"allocations": {U("allocation", 0): "add"}}
            return ["allocation", "bind"], {"assignment_id": "as-0", "allocations": {}}
        if k == "alDeleteAssignment":
            return ["allocation", "delete", "--assignment", f"as-{a['assignment']}"], None
        if k == "alDeleteIds":
            ids = [U("allocation", _one(r)[1]["k"]) if _one(r)[0] == "id" else "" for r in a["ids"]]
            return ["allocation", "delete", "--ids", ",".join(ids)], None
        if k == "alDeleteNeither":
            return ["allocation", "delete"], None
        if k == "unitCreate":
            p = {"category": f"cat-{a['category']}", "manufacturer": "acme", "host": f"host-{a['host']}", "capacity": a["capacity"]}
            if a["caps"]:
                p["capabilities"] = [f"cap-{c}" for c in a["caps"]]
            if a["parent"] is not None:
                p["parent_uuid"] = U("unit", a["parent"])
            return ["unit", "create"], p
        if k == "unitCreateBad":
            p = {"category": "cat-0", "manufacturer": "acme", "host": "host-0", "capacity": 1}
            bad = a["bad"]
            if bad == "emptyCategory": p["category"] = ""
            elif bad == "negativeCapacity": p["capacity"] = -1
            elif bad == "boolCapacity": p["capacity"] = True
            elif bad == "systemField": p["status"] = "active"
            elif bad == "capsNotList": p["capabilities"] = "cap-0"
            elif bad == "emptyParent": p["parent_uuid"] = ""
            return ["unit", "create"], p
        if k in ("unitActivate", "unitDeactivate", "unitGet"):
            return ["unit", {"unitActivate": "activate", "unitDeactivate": "deactivate", "unitGet": "get"}[k], U("unit", a["id"])], None
        if k == "unitList":
            fk, fa = _one(a["filter"])
            extra = {"all": [], "category": ["--category", f"cat-{fa.get('c')}"], "host": ["--host", f"host-{fa.get('h')}"],
                     "status": ["--filter", "status=" + ("active" if fa.get("active") else "inactive")],
                     "capacity": ["--filter", f"capacity={fa.get('n')}"], "parentNull": ["--filter", "parent_uuid=null"],
                     "unknownField": ["--filter", "nosuchfield=x"], "malformed": ["--filter", "status"]}[fk]
            return ["unit", "list", *extra], None
        if k == "ledger":
            return ["ledger", "capabilities"], None
        if k == "modCreate":
            p = {"unit_uuid": U("unit", a["unit"]), "component_type": f"ct-{a['ctype']}"}
            fk, fa = _one(a["firmware"])
            if fk == "null": p["firmware_image"] = None
            elif fk == "image": p["firmware_image"] = self.image(fa["i"])
            elif fk == "invalid": p["firmware_image"] = "NOT-AN-IMAGE"
            return ["module", "create"], p
        if k == "modCreateEmptyType":
            return ["module", "create"], {"unit_uuid": U("unit", a["unit"]), "component_type": ""}
        if k == "modList":
            fk, fa = _one(a["filter"])
            extra = {"all": [], "unit": ["--unit", U("unit", fa.get("u", 0))], "ctype": ["--filter", f"component_type=ct-{fa.get('c')}"],
                     "firmwareNull": ["--filter", "firmware_image=null"], "unknownField": ["--filter", "nosuchfield=x"],
                     "malformed": ["--filter", "component_type"]}[fk]
            return ["module", "list", *extra], None
        if k == "modGet":
            return ["module", "get", U("module", a["id"])], None
        if k == "modFlash":
            ik, ia = _one(a["image"])
            img = self.image(ia["i"]) if ik == "image" else "NOT-AN-IMAGE"
            return ["module", "flash", U("module", a["id"]), "--image", img], None
        if k == "tagCreate":
            return ["tag", "create"], {"module_uuid": U("module", a["module"]), "key": f"key-{a['key']}", "value": f"val-{a['value']}"}
        if k == "tagCreateEmptyKey":
            return ["tag", "create"], {"module_uuid": U("module", a["module"]), "key": "", "value": "val-0"}
        if k == "tagList":
            args = ["tag", "list"]
            if a["module"] is not None:
                args += ["--module", U("module", a["module"])]
            if a["key"] is not None:
                args += ["--key", f"key-{a['key']}"]
            return args, None
        if k == "tagGet":
            return ["tag", "get", U("tag", a["id"])], None
        if k == "tagDelete":
            return ["tag", "delete", U("tag", a["id"])], None
        if k == "tagUpdate":
            return ["tag", "update", "x"], None
        if k in ("schemaInit", "schemaVersion", "schemaUpgrade"):
            return ["schema", k[len("schema"):].lower()], None
        raise ValueError(f"unknown command {cmd}")

    def call(self, args: list[str], payload: dict | None):
        result = subprocess.run(
            [sys.executable, str(self.program), "--data-dir", str(self.data_dir), *args],
            input="" if payload is None else json.dumps(payload), capture_output=True, text=True, timeout=60)
        try:
            body = json.loads(result.stdout)
        except json.JSONDecodeError:
            body = {"unparseable": result.stdout[:200], "stderr": result.stderr[-300:]}
        return result.returncode, body

    # Abstraction -----------------------------------------------------------------------

    @staticmethod
    def num(value, prefix: str):
        if isinstance(value, str) and value.startswith(prefix) and value[len(prefix):].isdigit():
            return int(value[len(prefix):])
        return f"unexpected:{value!r}"

    def req_cap_num(self, c):
        return 0 if c == "category=TYPE_A" else self.num(c, "cap-")

    def bp_view(self, b: dict) -> dict:
        sets = [{"rtype": self.num(r.get("resource_type"), "rt-"), "count": r.get("resource_count"),
                 "caps": [self.req_cap_num(c) for c in r["capabilities"]] if "capabilities" in r else None}
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
        mods = [self.id("module", m) for m in u.get("module_uuids", [])]
        return {"id": self.id("unit", u.get("uuid")), "category": self.num(u.get("category"), "cat-"),
                "host": self.num(u.get("host"), "host-"), "capacity": u.get("capacity"),
                "parent": None if u.get("parent_uuid") is None else self.id("unit", u["parent_uuid"]),
                "caps": [self.num(c, "cap-") for c in u.get("capabilities", [])], "status": u.get("status"),
                "reservedCapacity": u.get("reserved_capacity"),
                # module_uuids are sorted by UUID, which is random; the model keeps creation order.
                "modules": sorted(mods, key=lambda x: (not isinstance(x, int), x if isinstance(x, int) else 0))}

    def module_view(self, m: dict) -> dict:
        fw = m.get("firmware_image")
        return {"id": self.id("module", m.get("uuid")), "unit": self.id("unit", m.get("unit_uuid")),
                "ctype": self.num(m.get("component_type"), "ct-"),
                "firmware": None if fw is None else self.num(fw, IMAGE_PREFIX)}

    def tag_view(self, t: dict) -> dict:
        return {"id": self.id("tag", t.get("uuid")), "module": self.id("module", t.get("module_uuid")),
                "key": self.num(t.get("key"), "key-"), "value": self.num(t.get("value"), "val-")}

    @staticmethod
    def _key(v, *fields):
        return tuple((not isinstance(v[f], int), v[f] if isinstance(v[f], int) else 0) for f in fields)

    def tie_canonical(self, items: list, group_fields: tuple, view) -> list:
        """Within runs of equal `group_fields` (e.g. equal created_at), order by abstract id:
        the code breaks such ties by random UUID, which the model cannot predict."""
        out, i = [], 0
        while i < len(items):
            j = i
            key = tuple(items[i].get(f) for f in group_fields)
            while j < len(items) and tuple(items[j].get(f) for f in group_fields) == key:
                j += 1
            out += sorted((view(x) for x in items[i:j]), key=lambda v: self._key(v, "id"))
            i = j
        return out

    def body(self, cmd, code: int, resp) -> dict:
        k, _ = _one(cmd)
        if isinstance(resp, dict) and "error" in resp:
            err = {"error": {"category": resp["error"]}}
            return err if code == 1 else {"exitCodeMismatch": {"code": code, "body": err}}
        if code != 0:
            return {"exitCodeMismatch": {"code": code, "body": resp}}
        if not isinstance(resp, dict):
            return {"unexpected": resp}
        r = {key: v for key, v in resp.items() if key != "revision"}
        try:
            if k == "version":
                return "versionInfo" if r == {"max_revision": "1.2", "min_revision": "1.0"} else {"unexpected": r}
            if k in ("bpCreate", "bpGet", "bpGetByName"):
                return {"blueprint": {"b": self.bp_view(r)}}
            if k == "bpList":
                return {"blueprints": {"bs": self.tie_canonical(r["blueprints"], ("created_at",), self.bp_view)}}
            if k in ("bpDelete", "bpDeleteNames", "alDeleteAssignment", "alDeleteIds", "tagDelete"):
                return {"deleted": {"n": r["deleted"]}}
            if k == "alCreate":
                return {"allocations": {"as": [self.alloc_view(a) for a in r["allocations"]]}}
            if k == "alList":
                return {"allocations": {"as": self.tie_canonical(r["allocations"], ("created_at",), self.alloc_view)}}
            if k == "alGet":
                return {"allocation": {"a": self.alloc_view(r)}}
            if k == "alBind":
                return "accepted" if r == {"status": "accepted"} else {"unexpected": r}
            if k in ("unitCreate", "unitActivate", "unitDeactivate", "unitGet"):
                return {"unit": {"u": self.unit_view(r)}}
            if k == "unitList":
                return {"units": {"us": self.tie_canonical(r["units"], ("host", "category", "created_at"), self.unit_view)}}
            if k == "ledger":
                return {"caps": {"cs": [self.num(c, "cap-") for c in r["capabilities"]]}}
            if k in ("modCreate", "modGet", "modFlash"):
                return {"module": {"m": self.module_view(r)}}
            if k == "modList":
                views = [self.module_view(m) for m in r["modules"]]
                return {"modules": {"ms": sorted(views, key=lambda v: self._key(v, "unit", "id"))}}
            if k in ("tagCreate", "tagGet"):
                return {"tag": {"t": self.tag_view(r)}}
            if k == "tagList":
                views = [self.tag_view(t) for t in r["tags"]]
                return {"tags": {"ts": sorted(views, key=lambda v: self._key(v, "module", "key", "id"))}}
            if k == "schemaInit" or k == "schemaUpgrade":
                return {"schemaStatus": {"status": r.get("status"), "version": r.get("version")}}
            if k == "schemaVersion":
                return {"schemaVersion": {"version": r["version"]}}
        except (KeyError, TypeError, AttributeError):
            return {"unexpected": r}
        return {"unexpected": r}

    def register(self, cmd, resp) -> None:
        """Record the UUIDs a successful create returned, in order."""
        k, _ = _one(cmd)
        if not isinstance(resp, dict) or "error" in resp:
            return
        kind = {"bpCreate": "blueprint", "unitCreate": "unit", "modCreate": "module", "tagCreate": "tag"}.get(k)
        if kind and resp.get("uuid") and resp["uuid"] not in self.ids[kind]:
            self.ids[kind].append(resp["uuid"])
        if k == "alCreate":
            for a in resp.get("allocations", []):
                if a.get("uuid") not in self.ids["allocation"]:
                    self.ids["allocation"].append(a["uuid"])

    def run(self, action) -> dict:
        args, payload = self.command(action["cmd"])
        code, resp = self.call(self.flags(action["ctx"]) + args, payload)
        self.register(action["cmd"], resp)
        revision = resp.get("revision") if isinstance(resp, dict) else None
        return {"body": self.body(action["cmd"], code, resp), "revision": revision}

    def observe(self) -> dict:
        reads = {
            "blueprints": ["blueprint", "list"], "allocations": ["--revision", "latest", "allocation", "list"],
            "units": ["unit", "list"], "ledger": ["ledger", "capabilities"], "modules": ["module", "list"],
            "tags": ["tag", "list"],
        }
        got = {}
        for name, args in reads.items():
            code, resp = self.call(args, None)
            key = "capabilities" if name == "ledger" else name
            if code != 0 or not isinstance(resp, dict) or key not in resp:
                return {"unobservable": {name: resp}}
            got[name] = resp[key]
        code, schema = self.call(["schema", "version"], None)
        by_id = lambda vs: sorted(vs, key=lambda v: self._key(v, "id"))
        return {
            "blueprints": by_id([self.bp_view(b) for b in got["blueprints"]]),
            "allocations": by_id([self.alloc_view(a) for a in got["allocations"]]),
            "units": by_id([self.unit_view(u) for u in got["units"]]),
            "ledger": [self.num(c, "cap-") for c in got["ledger"]],
            "modules": by_id([self.module_view(m) for m in got["modules"]]),
            "tags": sorted((self.tag_view(t) for t in got["tags"]), key=lambda v: self._key(v, "module", "key", "id")),
            "schema": schema.get("version") if code == 0 and isinstance(schema, dict) else None,
        }
