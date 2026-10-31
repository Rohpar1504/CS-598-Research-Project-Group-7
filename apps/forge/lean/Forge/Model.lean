import Companion.Core

/-!
Model of the whole forge app (SlopCodeBench `forge`, reference solution of checkpoint 8):
blueprints, allocations and binding, units and the ledger, modules and firmware, tags, the
schema lifecycle, revision negotiation and access control. It follows the reference code,
including its order of error checks. Not modeled: `check readiness` and plugin/config
validation (they depend on configuration files, not on application state).

Abstraction (the adapter implements the mapping):
* Every resource's abstract id is its position in that resource's creation order.
* Names and values are naturals: blueprint `bp-n`, resource type `rt-n`, assignment and
  tenant ids `as-n` and `tn-n`, category `cat-n`, host `host-n`, unit capability `cap-n`;
  in blueprint requirement sets, capability 0 is `category=TYPE_A` and n > 0 is `cap-n`;
  component type `ct-n`; tag key and value `key-n` and `val-n`; firmware image n is a fixed
  lowercase UUID.
* `created_at`, `manufacturer` and `detail` messages are not modeled. List orders that depend
  on random UUIDs or on creation timestamps are canonicalized by the adapter.
-/

namespace Forge

open Lean

/-! ## State -/

structure ReqSet where
  rtype : Nat
  count : Nat
  caps  : Option (List Nat)     -- absent, or the list as given
deriving BEq, Hashable, Repr, ToJson, FromJson

structure Blueprint where
  id   : Nat
  name : Nat
  sets : List ReqSet
deriving BEq, Hashable, Repr

structure Allocation where
  id         : Nat
  blueprint  : Nat              -- blueprint name; kept if the blueprint is deleted
  setIndex   : Nat
  assignment : Option Nat
  bound      : Bool
  tenant     : Option Nat
deriving BEq, Hashable, Repr

structure FUnit where
  category : Nat
  host     : Nat
  capacity : Nat
  parent   : Option Nat
  caps     : List Nat
  active   : Bool
  modules  : List Nat           -- module ids, as stored on the unit
deriving BEq, Hashable, Repr

structure Module where
  unit     : Nat
  ctype    : Nat
  firmware : Option Nat
deriving BEq, Hashable, Repr

structure Tag where
  id     : Nat
  module : Nat
  key    : Nat
  value  : Nat
deriving BEq, Hashable, Repr

structure State where
  blueprints     : List Blueprint := []
  nextBlueprint  : Nat := 0
  allocations    : List Allocation := []
  nextAllocation : Nat := 0
  units          : Array FUnit := #[]
  modules        : Array Module := #[]
  tags           : List Tag := []
  nextTag        : Nat := 0
  schema         : Option Nat := none
deriving BEq, Hashable, Repr

/-! ## Actions -/

/-- `--revision`. Omitted means 1.0; `latest` means 1.2. -/
inductive Rev where
  | omitted | r10 | r11 | r12 | latest | unparseable | unsupported
deriving DecidableEq, Repr, ToJson, FromJson

inductive ScopeArg where
  | omitted | project | system | invalid
deriving DecidableEq, Repr, ToJson, FromJson

inductive Role where
  | admin | user
deriving DecidableEq, Repr, ToJson, FromJson

/-- Global flags. `rules` means a rules file requiring role `admin` for blueprint create
and delete is passed with `--rules-file`. -/
structure Ctx where
  rev   : Rev := .omitted
  scope : ScopeArg := .omitted
  rules : Bool := false
  role  : Option Role := none
deriving BEq, Repr, ToJson, FromJson

inductive BpBad where
  | emptyName | noSets | zeroCount | boolCount | unsupportedKey | capsNotList
deriving BEq, Repr, ToJson, FromJson

inductive NameRef where
  | name (n : Nat) | empty
deriving BEq, Repr, ToJson, FromJson

inductive Op where
  | add | remove | bogus
deriving BEq, Repr, ToJson, FromJson

inductive TenantArg where
  | absent | null | value (t : Nat) | notString
deriving BEq, Repr, ToJson, FromJson

inductive BindBad where
  | noAssignment | noAllocations
deriving BEq, Repr, ToJson, FromJson

inductive IdRef where
  | id (k : Nat) | empty
deriving BEq, Repr, ToJson, FromJson

inductive UnitBad where
  | emptyCategory | negativeCapacity | boolCapacity | systemField | capsNotList | emptyParent
deriving BEq, Repr, ToJson, FromJson

inductive UnitFilter where
  | all | category (c : Nat) | host (h : Nat) | status (active : Bool) | capacity (n : Nat)
  | parentNull | unknownField | malformed
deriving BEq, Repr, ToJson, FromJson

inductive FwArg where
  | absent | null | image (i : Nat) | invalid
deriving BEq, Repr, ToJson, FromJson

inductive ImageArg where
  | image (i : Nat) | invalid
deriving BEq, Repr, ToJson, FromJson

inductive ModFilter where
  | all | unit (u : Nat) | ctype (c : Nat) | firmwareNull | unknownField | malformed
deriving BEq, Repr, ToJson, FromJson

inductive Cmd where
  | version
  | bpCreate (name : Nat) (sets : List ReqSet)
  | bpCreateBad (bad : BpBad)
  | bpList
  | bpGet (id : Nat)
  | bpGetByName (name : Nat)
  | bpDelete (id : Nat)
  | bpDeleteNames (names : List NameRef)
  | alCreate (blueprint : Nat)
  | alCreateEmptyName
  | alList (assignment : Option Nat) (bound : Option Bool)
  | alGet (id : Nat)
  | alBind (assignment : Nat) (ops : List (Nat × Op)) (tenant : TenantArg)
  | alBindBad (bad : BindBad)
  | alDeleteAssignment (assignment : Nat)
  | alDeleteIds (ids : List IdRef)
  | alDeleteNeither
  | unitCreate (category host capacity : Nat) (parent : Option Nat) (caps : List Nat)
  | unitCreateBad (bad : UnitBad)
  | unitActivate (id : Nat)
  | unitDeactivate (id : Nat)
  | unitGet (id : Nat)
  | unitList (filter : UnitFilter)
  | ledger
  | modCreate (unit ctype : Nat) (firmware : FwArg)
  | modCreateEmptyType (unit : Nat)
  | modList (filter : ModFilter)
  | modGet (id : Nat)
  | modFlash (id : Nat) (image : ImageArg)
  | tagCreate (module key value : Nat)
  | tagCreateEmptyKey (module : Nat)
  | tagList (module : Option Nat) (key : Option Nat)
  | tagGet (id : Nat)
  | tagDelete (id : Nat)
  | tagUpdate
  | schemaInit
  | schemaVersion
  | schemaUpgrade
deriving BEq, Repr, ToJson, FromJson

structure Action where
  cmd : Cmd
  ctx : Ctx := {}
deriving BEq, Repr, ToJson, FromJson

/-! ## Outputs and observations -/

structure BlueprintView where
  id   : Nat
  name : Nat
  sets : List ReqSet
deriving BEq, Repr, ToJson, FromJson

/-- `tenant_id` is shown only at revision 1.1 or higher. -/
inductive TenantView where
  | hidden | null | value (t : Nat)
deriving BEq, Repr, ToJson, FromJson

structure AllocView where
  id         : Nat
  blueprint  : Nat
  setIndex   : Nat
  assignment : Option Nat
  status     : String
  tenant     : TenantView
deriving BEq, Repr, ToJson, FromJson

structure UnitView where
  id               : Nat
  category         : Nat
  host             : Nat
  capacity         : Nat
  parent           : Option Nat
  caps             : List Nat
  status           : String
  reservedCapacity : Nat
  modules          : List Nat
deriving BEq, Repr, ToJson, FromJson

structure ModuleView where
  id       : Nat
  unit     : Nat
  ctype    : Nat
  firmware : Option Nat
deriving BEq, Repr, ToJson, FromJson

structure TagView where
  id     : Nat
  module : Nat
  key    : Nat
  value  : Nat
deriving BEq, Repr, ToJson, FromJson

inductive Body where
  | blueprint (b : BlueprintView)
  | blueprints (bs : List BlueprintView)
  | allocation (a : AllocView)
  | allocations (as : List AllocView)
  | unit (u : UnitView)
  | units (us : List UnitView)
  | caps (cs : List Nat)
  | module (m : ModuleView)
  | modules (ms : List ModuleView)
  | tag (t : TagView)
  | tags (ts : List TagView)
  | deleted (n : Nat)
  | accepted
  | schemaStatus (status : String) (version : Option String)
  | schemaVersion (version : String)
  | versionInfo
  | error (category : String)
deriving BEq, Repr, ToJson, FromJson

/-- Every response but `version` carries the negotiated revision. -/
structure Out where
  body     : Body
  revision : Option String
deriving BEq, Repr, ToJson, FromJson

/-- Everything the read commands show. Allocations are read at revision 1.2, so their
tenants are visible. -/
structure Obs where
  blueprints  : List BlueprintView
  allocations : List AllocView
  units       : List UnitView
  ledger      : List Nat
  modules     : List ModuleView
  tags        : List TagView
  schema      : Option String
deriving BEq, Repr, ToJson, FromJson

/-! ## Views -/

def bpView (b : Blueprint) : BlueprintView := { id := b.id, name := b.name, sets := b.sets }

def allocView (minor : Nat) (a : Allocation) : AllocView :=
  { id := a.id, blueprint := a.blueprint, setIndex := a.setIndex, assignment := a.assignment,
    status := if a.bound then "bound" else "unbound",
    tenant := if minor < 1 then .hidden else match a.tenant with
      | none => .null
      | some t => .value t }

def unitView (id : Nat) (u : FUnit) : UnitView :=
  { id, category := u.category, host := u.host, capacity := u.capacity, parent := u.parent,
    caps := u.caps, status := if u.active then "active" else "inactive",
    reservedCapacity := if u.active then 0 else u.capacity, modules := u.modules }

def unitViews (s : State) : List UnitView :=
  s.units.toList.zipIdx.map fun (u, i) => unitView i u

def moduleView (id : Nat) (m : Module) : ModuleView :=
  { id, unit := m.unit, ctype := m.ctype, firmware := m.firmware }

def moduleViews (s : State) : List ModuleView :=
  s.modules.toList.zipIdx.map fun (m, i) => moduleView i m

def tagView (t : Tag) : TagView := { id := t.id, module := t.module, key := t.key, value := t.value }

def ledgerOf (s : State) : List Nat :=
  ((s.units.toList.filter (·.active)).flatMap (·.caps)).eraseDups.mergeSort

def unitPasses (f : UnitFilter) (u : UnitView) : Bool :=
  match f with
  | .all => true
  | .category c => u.category == c
  | .host h => u.host == h
  | .status a => u.status == (if a then "active" else "inactive")
  | .capacity n => u.capacity == n
  | .parentNull => u.parent.isNone
  | .unknownField | .malformed => false

def unitOrder (a b : UnitView) : Bool :=
  a.host < b.host || (a.host == b.host &&
    (a.category < b.category || (a.category == b.category && a.id ≤ b.id)))

def modPasses (f : ModFilter) (m : ModuleView) : Bool :=
  match f with
  | .all => true
  | .unit u => m.unit == u
  | .ctype c => m.ctype == c
  | .firmwareNull => m.firmware.isNone
  | .unknownField | .malformed => false

/-- Modules are listed by parent unit UUID, which is random, so the adapter canonicalizes to
unit id, then creation. -/
def modOrder (a b : ModuleView) : Bool := a.unit < b.unit || (a.unit == b.unit && a.id ≤ b.id)

/-- Tags are listed by module UUID (canonicalized to module id), key, then UUID (canonicalized
to creation). -/
def tagOrder (a b : TagView) : Bool :=
  a.module < b.module || (a.module == b.module &&
    (a.key < b.key || (a.key == b.key && a.id ≤ b.id)))

def observe (s : State) : Obs :=
  { blueprints := s.blueprints.map bpView, allocations := s.allocations.map (allocView 2),
    units := unitViews s, ledger := ledgerOf s, modules := moduleViews s,
    tags := (s.tags.map tagView).mergeSort tagOrder, schema := s.schema.map toString }

/-! ## Step -/

def negotiate : Rev → Except String Nat
  | .omitted | .r10 => .ok 0
  | .r11 => .ok 1
  | .r12 | .latest => .ok 2
  | .unparseable => .error "parse_error"
  | .unsupported => .error "unsupported_revision"

/-- Access check for blueprint create and delete. -/
def accessError (ctx : Ctx) : Option String :=
  if ctx.scope == .system then some "forbidden"
  else if !ctx.rules then none
  else if ctx.role == some .admin then none
  else some "forbidden"

/-- Requirement sets naming capability 0 (`category=TYPE_A`) yield two allocations. -/
def multiplicity (r : ReqSet) : Nat :=
  match r.caps with
  | some cs => if cs.contains 0 then 2 else 1
  | none => 1

def expand (bpName : Nat) (sets : List ReqSet) (next : Nat) : List Allocation :=
  let slots := (sets.zipIdx).flatMap fun (r, i) => List.replicate (multiplicity r) i
  slots.zipIdx.map fun (i, k) =>
    { id := next + k, blueprint := bpName, setIndex := i, assignment := none, bound := false,
      tenant := none }

def existsUnit (s : State) : Option Nat → Bool
  | none => true
  | some p => p < s.units.size

def knownImages (s : State) : List Nat := s.modules.toList.filterMap (·.firmware)

def bindOps (as : List Allocation) (a : Nat) (ops : List (Nat × Op)) (tenant : TenantArg) :
    List Allocation :=
  as.map fun al =>
    match ops.find? (·.1 == al.id) with
    | none => al
    | some (_, op) =>
      let al := match op with
        | .add => { al with bound := true, assignment := some a }
        | _ => { al with bound := false, assignment := none }
      match tenant with
      | .absent | .notString => al
      | .null => { al with tenant := none }
      | .value t => { al with tenant := some t }

/-- One command at negotiated minor revision `minor`. -/
def run (s : State) (minor : Nat) (ctx : Ctx) : Cmd → State × Body
  | .version => (s, .versionInfo)
  -- Blueprints
  | .bpCreate name sets =>
    match accessError ctx with
    | some e => (s, .error e)
    | none =>
      if s.blueprints.any (·.name == name) then (s, .error "conflict")
      else
        let b : Blueprint := { id := s.nextBlueprint, name, sets }
        ({ s with blueprints := s.blueprints ++ [b], nextBlueprint := s.nextBlueprint + 1 },
         .blueprint (bpView b))
  | .bpCreateBad _ =>
    match accessError ctx with
    | some e => (s, .error e)
    | none => (s, .error "validation_error")
  | .bpList => (s, .blueprints (s.blueprints.map bpView))
  | .bpGet id =>
    match s.blueprints.find? (·.id == id) with
    | some b => (s, .blueprint (bpView b))
    | none => (s, .error "not_found")
  | .bpGetByName name =>
    if minor < 2 then (s, .error "revision_too_low")
    else match s.blueprints.find? (·.name == name) with
      | some b => (s, .blueprint (bpView b))
      | none => (s, .error "not_found")
  | .bpDelete id =>
    match accessError ctx with
    | some e => (s, .error e)
    | none =>
      if s.blueprints.any (·.id == id) then
        ({ s with blueprints := s.blueprints.filter (·.id != id) }, .deleted 1)
      else (s, .error "not_found")
  | .bpDeleteNames names =>
    match accessError ctx with
    | some e => (s, .error e)
    | none =>
      let resolved : NameRef → Bool
        | .name n => s.blueprints.any (·.name == n)
        | .empty => false
      if !names.all resolved then (s, .error "not_found")
      else
        let doomed (b : Blueprint) := names.contains (.name b.name)
        let remaining := s.blueprints.filter (!doomed ·)
        ({ s with blueprints := remaining }, .deleted (s.blueprints.length - remaining.length))
  -- Allocations
  | .alCreate name =>
    match s.blueprints.find? (·.name == name) with
    | none => (s, .error "not_found")
    | some b =>
      let created := expand name b.sets s.nextAllocation
      ({ s with allocations := s.allocations ++ created,
                nextAllocation := s.nextAllocation + created.length },
       .allocations (created.map (allocView minor)))
  | .alCreateEmptyName => (s, .error "validation_error")
  | .alList assignment bound =>
    let keep (a : Allocation) := (assignment.all (a.assignment == some ·)) && (bound.all (a.bound == ·))
    (s, .allocations ((s.allocations.filter keep).map (allocView minor)))
  | .alGet id =>
    match s.allocations.find? (·.id == id) with
    | some a => (s, .allocation (allocView minor a))
    | none => (s, .error "not_found")
  | .alBind a ops tenant =>
    if ops.isEmpty then (s, .error "validation_error")
    else if tenant != .absent && minor < 1 then (s, .error "revision_too_low")
    else if tenant == .notString then (s, .error "validation_error")
    else if ops.any (·.2 == .bogus) then (s, .error "invalid_operation")
    else if !ops.all (fun (id, _) => s.allocations.any (·.id == id)) then (s, .error "not_found")
    else if ops.any (·.2 == .add) && s.allocations.any (fun al => al.bound && al.assignment == some a)
      then (s, .error "duplicate_assignment")
    else ({ s with allocations := bindOps s.allocations a ops tenant }, .accepted)
  | .alBindBad _ => (s, .error "validation_error")
  | .alDeleteAssignment a =>
    let remaining := s.allocations.filter (·.assignment != some a)
    ({ s with allocations := remaining }, .deleted (s.allocations.length - remaining.length))
  | .alDeleteIds ids =>
    let resolved : IdRef → Bool
      | .id k => s.allocations.any (·.id == k)
      | .empty => false
    if !ids.all resolved then (s, .error "not_found")
    else
      let remaining := s.allocations.filter fun al => !ids.contains (.id al.id)
      ({ s with allocations := remaining }, .deleted (s.allocations.length - remaining.length))
  | .alDeleteNeither => (s, .error "validation_error")
  -- Units
  | .unitCreate category host capacity parent caps =>
    if !existsUnit s parent then (s, .error "not_found")
    else
      let u : FUnit := { category, host, capacity, parent, caps := caps.mergeSort, active := true,
                         modules := [] }
      ({ s with units := s.units.push u }, .unit (unitView s.units.size u))
  | .unitCreateBad _ => (s, .error "validation_error")
  | .unitActivate id =>
    match s.units[id]? with
    | none => (s, .error "not_found")
    | some u =>
      if u.active then (s, .error "invalid_transition")
      else
        let u' := { u with active := true }
        ({ s with units := s.units.set! id u' }, .unit (unitView id u'))
  | .unitDeactivate id =>
    match s.units[id]? with
    | none => (s, .error "not_found")
    | some u =>
      if !u.active then (s, .error "invalid_transition")
      else
        let u' := { u with active := false }
        ({ s with units := s.units.set! id u' }, .unit (unitView id u'))
  | .unitGet id =>
    match s.units[id]? with
    | none => (s, .error "not_found")
    | some u => (s, .unit (unitView id u))
  | .unitList .malformed => (s, .error "validation_error")
  | .unitList f => (s, .units (((unitViews s).filter (unitPasses f)).mergeSort unitOrder))
  | .ledger => (s, .caps (ledgerOf s))
  -- Modules
  | .modCreate unit ctype fw =>
    if fw == .invalid then (s, .error "invalid_image")
    else match s.units[unit]? with
      | none => (s, .error "not_found")
      | some u =>
        let m : Module := { unit, ctype, firmware := match fw with | .image i => some i | _ => none }
        let id := s.modules.size
        ({ s with modules := s.modules.push m,
                  units := s.units.set! unit { u with modules := u.modules ++ [id] } },
         .module (moduleView id m))
  | .modCreateEmptyType _ => (s, .error "validation_error")
  | .modList .malformed => (s, .error "validation_error")
  | .modList f => (s, .modules (((moduleViews s).filter (modPasses f)).mergeSort modOrder))
  | .modGet id =>
    match s.modules[id]? with
    | none => (s, .error "not_found")
    | some m => (s, .module (moduleView id m))
  | .modFlash id img =>
    match img with
    | .invalid => (s, .error "invalid_image")
    | .image i =>
      match s.modules[id]? with
      | none => (s, .error "not_found")
      | some m =>
        if !(knownImages s).contains i then (s, .error "flash_failed")
        else
          let m' := { m with firmware := some i }
          ({ s with modules := s.modules.set! id m' }, .module (moduleView id m'))
  -- Tags
  | .tagCreate module key value =>
    if module ≥ s.modules.size then (s, .error "not_found")
    else if s.tags.any (fun t => t.module == module && t.key == key) then (s, .error "conflict")
    else
      let t : Tag := { id := s.nextTag, module, key, value }
      ({ s with tags := s.tags ++ [t], nextTag := s.nextTag + 1 }, .tag (tagView t))
  | .tagCreateEmptyKey _ => (s, .error "validation_error")
  | .tagList module key =>
    let keep (t : Tag) := module.all (t.module == ·) && key.all (t.key == ·)
    (s, .tags (((s.tags.filter keep).map tagView).mergeSort tagOrder))
  | .tagGet id =>
    match s.tags.find? (·.id == id) with
    | some t => (s, .tag (tagView t))
    | none => (s, .error "not_found")
  | .tagDelete id =>
    if s.tags.any (·.id == id) then ({ s with tags := s.tags.filter (·.id != id) }, .deleted 1)
    else (s, .error "not_found")
  | .tagUpdate => (s, .error "unknown_command")
  -- Schema
  | .schemaInit =>
    match s.schema with
    | some _ => (s, .error "conflict")
    | none => ({ s with schema := some 1 }, .schemaStatus "created" none)
  | .schemaVersion =>
    match s.schema with
    | none => (s, .error "not_found")
    | some v => (s, .schemaVersion (toString v))
  | .schemaUpgrade =>
    match s.schema with
    | none => (s, .error "not_found")
    | some v => (s, .schemaStatus "up_to_date" (some (toString v)))

/-- Global checks come first, in the code's order: revision, then scope. -/
def step (s : State) (a : Action) : State × Out :=
  match negotiate a.ctx.rev with
  | .error e => (s, { body := .error e, revision := some "1.0" })
  | .ok minor =>
    let rev := some s!"1.{minor}"
    if a.ctx.scope == .invalid then (s, { body := .error "validation_error", revision := rev })
    else
      let (s', body) := run s minor a.ctx a.cmd
      (s', { body, revision := if a.cmd == .version then none else rev })

/-! ## Exploration scopes -/

def act (c : Cmd) : Action := { cmd := c }

/-- A search scope: an action alphabet and a bound on the number of each resource. -/
structure Scope where
  alphabet : List Action
  bound    : State → Bool

def small (s : State) : Bool :=
  s.blueprints.length ≤ 2 && s.nextBlueprint ≤ 3 && s.allocations.length ≤ 4 &&
  s.nextAllocation ≤ 5 && s.units.size ≤ 2 && s.modules.size ≤ 2 && s.tags.length ≤ 2 &&
  s.nextTag ≤ 3

def reqA : ReqSet := { rtype := 0, count := 1, caps := none }
def reqTypeA : ReqSet := { rtype := 1, count := 2, caps := some [0] }

/-- Blueprints, allocations and binding. -/
def allocScope : Scope :=
  { alphabet :=
      [act (.bpCreate 0 [reqA]), act (.bpCreate 1 [reqTypeA]), act (.bpDelete 0),
       act (.bpDeleteNames [.name 0, .name 1]), act (.alCreate 0), act (.alCreate 1),
       act (.alBind 0 [(0, .add)] .absent), act (.alBind 0 [(1, .add), (2, .add)] .absent),
       act (.alBind 1 [(0, .add)] .absent), act (.alBind 1 [(0, .remove), (1, .add)] .absent),
       act (.alBind 0 [(0, .add), (9, .add)] .absent), act (.alBind 0 [(1, .remove)] .absent),
       act (.alDeleteAssignment 0), act (.alDeleteIds [.id 0]), act (.alDeleteIds [.id 1, .id 9]),
       act (.alList none none)],
    bound := small }

/-- Units and the ledger. -/
def unitScope : Scope :=
  { alphabet :=
      [act (.unitCreate 0 1 2 none [1, 0]), act (.unitCreate 1 0 0 (some 0) []),
       act (.unitCreate 0 0 2 (some 5) [0]), act (.unitActivate 0), act (.unitDeactivate 0),
       act (.unitDeactivate 1), act (.unitList .all), act .ledger],
    bound := small }

/-- Modules, firmware and tags. -/
def moduleScope : Scope :=
  { alphabet :=
      [act (.unitCreate 0 0 1 none []), act (.modCreate 0 0 (.image 0)), act (.modCreate 0 1 .absent),
       act (.modCreate 1 0 .null), act (.modFlash 0 (.image 1)), act (.modFlash 1 (.image 0)),
       act (.modFlash 1 .invalid), act (.tagCreate 0 0 0), act (.tagCreate 0 0 1),
       act (.tagCreate 1 0 0), act (.tagDelete 0), act (.modList .all)],
    bound := small }

/-- Access control and revision gating. -/
def accessScope : Scope :=
  { alphabet :=
      [{ cmd := .bpCreate 0 [reqA], ctx := { rules := true, role := some .admin } },
       { cmd := .bpCreate 1 [reqA], ctx := { rules := true, role := some .user } },
       { cmd := .bpCreate 1 [reqA], ctx := { scope := .system } },
       { cmd := .bpDelete 0, ctx := { rules := true } },
       { cmd := .bpDelete 0, ctx := { scope := .system, role := some .admin } },
       { cmd := .bpGetByName 0, ctx := { rev := .r10 } },
       { cmd := .bpGetByName 0, ctx := { rev := .latest } },
       act (.alCreate 0),
       { cmd := .alBind 0 [(0, .add)] (.value 0), ctx := { rev := .r10 } },
       { cmd := .alBind 0 [(0, .add)] (.value 0), ctx := { rev := .r11 } },
       { cmd := .schemaInit, ctx := { rev := .unparseable } }, act .schemaInit, act .schemaUpgrade,
       { cmd := .bpList, ctx := { scope := .invalid } }],
    bound := small }

def scopeOf : String → Option Scope
  | "alloc" => some allocScope
  | "unit" => some unitScope
  | "module" => some moduleScope
  | "access" => some accessScope
  | _ => none

/-- The wide alphabet random validation traces draw from. -/
def validationAlphabet : List Action :=
  let revs : List Rev := [.omitted, .r10, .r11, .r12, .latest, .unparseable, .unsupported]
  let sets : List (List ReqSet) := [[reqA], [reqTypeA], [reqA, reqTypeA],
    [{ rtype := 2, count := 3, caps := some [] }], [{ rtype := 0, count := 1, caps := some [2, 0] }]]
  let ops : List (List (Nat × Op)) := [[(0, .add)], [(1, .add), (2, .add)], [(0, .remove)],
    [(1, .remove), (3, .add)], [(0, .add), (9, .add)], [(2, .bogus)], [(4, .add)]]
  let tenants : List TenantArg := [.absent, .null, .value 0, .value 1, .notString]
  let ctxs : List Ctx := [{}, { rules := true, role := some .admin }, { rules := true },
    { rules := true, role := some .user }, { scope := .system }, { scope := .project },
    { scope := .invalid }]
  (revs.map fun r => { cmd := .version, ctx := { rev := r } })
  ++ ((List.range 3).flatMap fun n => sets.flatMap fun ss => ctxs.map fun c => { cmd := .bpCreate n ss, ctx := c })
  ++ ([BpBad.emptyName, .noSets, .zeroCount, .boolCount, .unsupportedKey, .capsNotList].map (act ∘ .bpCreateBad))
  ++ [act .bpList] ++ ((List.range 4).map (act ∘ .bpGet))
  ++ ((List.range 3).flatMap fun n => revs.map fun r => { cmd := .bpGetByName n, ctx := { rev := r } })
  ++ ((List.range 3).flatMap fun n => ctxs.map fun c => { cmd := .bpDelete n, ctx := c })
  ++ ([[NameRef.name 0], [.name 1, .name 2], [.name 0, .empty], [.name 2, .name 7]].map (act ∘ .bpDeleteNames))
  ++ ((List.range 3).map (act ∘ .alCreate)) ++ [act .alCreateEmptyName]
  ++ ([none, some 0, some 1].flatMap fun a => [none, some true, some false].flatMap fun b =>
        [.r10, .r11].map fun r => { cmd := .alList a b, ctx := { rev := r } })
  ++ ((List.range 6).flatMap fun k => [.r10, .r12].map fun r => { cmd := .alGet k, ctx := { rev := r } })
  ++ ([0, 1, 2].flatMap fun a => ops.flatMap fun o => tenants.flatMap fun t =>
        [.r10, .r11].map fun r => { cmd := .alBind a o t, ctx := { rev := r } })
  ++ [act (.alBindBad .noAssignment), act (.alBindBad .noAllocations)]
  ++ ((List.range 3).map (act ∘ .alDeleteAssignment))
  ++ ([[IdRef.id 0], [.id 1, .id 2], [.id 0, .empty], [.id 3, .id 9]].map (act ∘ .alDeleteIds))
  ++ [act .alDeleteNeither]
  ++ ([0, 1].flatMap fun c => [0, 1].flatMap fun h => [0, 2].flatMap fun n =>
        [none, some 0, some 4].flatMap fun p => [[], [0], [2, 1]].map fun cs => act (.unitCreate c h n p cs))
  ++ ([UnitBad.emptyCategory, .negativeCapacity, .boolCapacity, .systemField, .capsNotList,
       .emptyParent].map (act ∘ .unitCreateBad))
  ++ ((List.range 4).flatMap fun i => [act (.unitActivate i), act (.unitDeactivate i), act (.unitGet i)])
  ++ ([UnitFilter.all, .category 1, .host 0, .status true, .capacity 2, .parentNull, .unknownField,
       .malformed].map (act ∘ .unitList)) ++ [act .ledger]
  ++ ((List.range 3).flatMap fun u => [0, 1].flatMap fun c =>
        [FwArg.absent, .null, .image 0, .image 1, .invalid].map fun f => act (.modCreate u c f))
  ++ ((List.range 2).map (act ∘ .modCreateEmptyType))
  ++ ([ModFilter.all, .unit 0, .ctype 1, .firmwareNull, .unknownField, .malformed].map (act ∘ .modList))
  ++ ((List.range 4).map (act ∘ .modGet))
  ++ ((List.range 4).flatMap fun m => [ImageArg.image 0, .image 1, .image 2, .invalid].map fun i => act (.modFlash m i))
  ++ ((List.range 3).flatMap fun m => [0, 1].flatMap fun k => [0, 1].map fun v => act (.tagCreate m k v))
  ++ [act (.tagCreateEmptyKey 0)]
  ++ ([none, some 0, some 1].flatMap fun m => [none, some 0].map fun k => act (.tagList m k))
  ++ ((List.range 4).flatMap fun t => [act (.tagGet t), act (.tagDelete t)]) ++ [act .tagUpdate]
  ++ [act .schemaInit, act .schemaVersion, act .schemaUpgrade]

def model (bound : State → Bool := fun _ => true) : Companion.Model State Action Out Obs :=
  { init := {}, step, observe, inScope := bound }

end Forge
