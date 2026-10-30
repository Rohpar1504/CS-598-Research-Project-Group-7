import Forge.Model

/-!
Properties of the forge app, as predicates on observed transitions.

`specProperties` restate rules from forge's specification; the reference solution should
satisfy them. `extraProperties` are rules a developer could plausibly state that the
specification does not make; the reference solution need not satisfy them, so they test
whether the companion finds violations in unmodified code.
-/

namespace Forge

open Companion

abbrev P := Property Action Out Obs

def Body.isError : Body → Bool
  | .error _ => true
  | _ => false

def Cmd.isRead : Cmd → Bool
  | .version | .bpList | .bpGet _ | .bpGetByName _ | .alList _ _ | .alGet _ | .unitGet _
  | .unitList _ | .ledger | .modList _ | .modGet _ | .tagList _ _ | .tagGet _ | .schemaVersion => true
  | _ => false

def Cmd.isBlueprintWrite : Cmd → Bool
  | .bpCreate _ _ | .bpCreateBad _ | .bpDelete _ | .bpDeleteNames _ => true
  | _ => false

/-! ### From the specification -/

def rejectedChangesNothing : P :=
  ⟨"a rejected request leaves the state unchanged", fun t => !t.out.body.isError || t.after == t.before⟩

def readsChangeNothing : P :=
  ⟨"read commands leave the state unchanged", fun t => !t.action.cmd.isRead || t.after == t.before⟩

def boundIffAssigned : P :=
  ⟨"an allocation is bound exactly when it has an assignment", fun t =>
    t.after.allocations.all fun a => (a.status == "bound") == a.assignment.isSome⟩

def oneBindPerAssignment : P :=
  ⟨"a bind that adds to an assignment succeeds only if the assignment has no bound allocations", fun t =>
    match t.action.cmd, t.out.body with
    | .alBind a ops _, .accepted =>
      !ops.any (·.2 == .add) || !t.before.allocations.any (fun al => al.status == "bound" && al.assignment == some a)
    | _, _ => true⟩

def typeADoubles : P :=
  ⟨"allocation create yields two allocations per TYPE_A requirement set and one per other set", fun t =>
    match t.action.cmd, t.out.body with
    | .alCreate n, .allocations as =>
      match t.before.blueprints.find? (·.name == n) with
      | some b => as.length == (b.sets.map multiplicity).sum
      | none => false
    | _, _ => true⟩

def ledgerExact : P :=
  ⟨"the ledger lists exactly the capabilities of active units", fun t =>
    t.after.ledger == ((t.after.units.filter (·.status == "active")).flatMap (·.caps)).eraseDups.mergeSort⟩

def parentExists : P :=
  ⟨"every unit's parent exists", fun t =>
    t.after.units.all fun u => u.parent.all fun p => t.after.units.any (·.id == p)⟩

def moduleLinks : P :=
  ⟨"a unit lists exactly the modules attached to it", fun t =>
    t.after.units.all fun u =>
      u.modules.mergeSort == ((t.after.modules.filter (·.unit == u.id)).map (·.id)).mergeSort⟩

def tagKeysUnique : P :=
  ⟨"no module has two tags with the same key", fun t =>
    let keys := t.after.tags.map fun g => (g.module, g.key)
    keys.eraseDups.length == keys.length⟩

def flashNeedsKnownImage : P :=
  ⟨"a flash succeeds only with an image some module already had", fun t =>
    match t.action.cmd, t.out.body with
    | .modFlash _ (.image i), .module _ => t.before.modules.any (·.firmware == some i)
    | _, _ => true⟩

def systemScopeForbidden : P :=
  ⟨"blueprint create and delete never succeed in system scope", fun t =>
    !(t.action.cmd.isBlueprintWrite && t.action.ctx.scope == .system) || t.out.body.isError⟩

def roleRequired : P :=
  ⟨"with an access rule, only an admin can create or delete blueprints", fun t =>
    !(t.action.cmd.isBlueprintWrite && t.action.ctx.rules && t.action.ctx.role != some .admin)
      || t.out.body.isError⟩

def revisionOnEveryResponse : P :=
  ⟨"every response except version carries a revision", fun t =>
    (t.action.cmd == .version) || t.out.revision.isSome⟩

def tenantNeedsRevision : P :=
  ⟨"tenant_id is shown only at revision 1.1 or higher", fun t =>
    let hidden (a : AllocView) := a.tenant == .hidden
    let lowRev := t.out.revision == some "1.0"
    match t.out.body with
    | .allocation a => lowRev == hidden a
    | .allocations as => as.all fun a => lowRev == hidden a
    | _ => true⟩

def specProperties : List P :=
  [rejectedChangesNothing, readsChangeNothing, boundIffAssigned, oneBindPerAssignment,
   typeADoubles, ledgerExact, parentExists, moduleLinks, tagKeysUnique, flashNeedsKnownImage,
   systemScopeForbidden, roleRequired, revisionOnEveryResponse, tenantNeedsRevision]

/-! ### Plausible developer rules the specification does not make -/

def allocationsHaveBlueprint : P :=
  ⟨"[extra] every allocation's blueprint still exists", fun t =>
    t.after.allocations.all fun a => t.after.blueprints.any (·.name == a.blueprint)⟩

def noSilentRebind : P :=
  ⟨"[extra] binding never moves an allocation that is bound to another assignment", fun t =>
    match t.action.cmd, t.out.body with
    | .alBind a ops _, .accepted =>
      ops.all fun (id, op) => op != .add ||
        (t.before.allocations.find? (·.id == id)).all fun al => al.status != "bound" || al.assignment == some a
    | _, _ => true⟩

def extraProperties : List P := [allocationsHaveBlueprint, noSilentRebind]

def properties : List P := specProperties ++ extraProperties

end Forge
