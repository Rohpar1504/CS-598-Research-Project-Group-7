import Forge.Model

/-!
Models that follow seeded-bug versions of the forge app (studies/feasibility/make_mutants.py).
In the companion's loop the model is updated to follow the code after an edit; these are
those updates, written by hand. Each overrides `run` for the commands the bug touches.
-/

namespace Forge

/-- `step` with a different command runner. -/
def stepWith (r : State → Nat → Ctx → Cmd → State × Body) (s : State) (a : Action) : State × Out :=
  match negotiate a.ctx.rev with
  | .error e => (s, { body := .error e, revision := some "1.0" })
  | .ok minor =>
    let rev := some s!"1.{minor}"
    if a.ctx.scope == .invalid then (s, { body := .error "validation_error", revision := rev })
    else
      let (s', body) := r s minor a.ctx a.cmd
      (s', { body, revision := if a.cmd == .version then none else rev })

/-- Follows `bind-no-duplicate-guard`: bind no longer refuses an assignment in use. -/
def runBindNoDuplicateGuard (s : State) (minor : Nat) (ctx : Ctx) : Cmd → State × Body
  | .alBind a ops tenant =>
    if ops.isEmpty then (s, .error "validation_error")
    else if tenant != .absent && minor < 1 then (s, .error "revision_too_low")
    else if tenant == .notString then (s, .error "validation_error")
    else if ops.any (·.2 == .bogus) then (s, .error "invalid_operation")
    else if !ops.all (fun (id, _) => s.allocations.any (·.id == id)) then (s, .error "not_found")
    else ({ s with allocations := bindOps s.allocations a ops tenant }, .accepted)
  | c => run s minor ctx c

/-- Follows `delete-names-partial`: unresolved names still let resolved ones be deleted. -/
def runDeleteNamesPartial (s : State) (minor : Nat) (ctx : Ctx) : Cmd → State × Body
  | .bpDeleteNames names =>
    match accessError ctx with
    | some e => (s, .error e)
    | none =>
      let resolved : NameRef → Bool
        | .name n => s.blueprints.any (·.name == n)
        | .empty => false
      let doomed (b : Blueprint) := names.contains (.name b.name)
      let remaining := s.blueprints.filter (!doomed ·)
      if !names.all resolved then ({ s with blueprints := remaining }, .error "not_found")
      else ({ s with blueprints := remaining }, .deleted (s.blueprints.length - remaining.length))
  | c => run s minor ctx c

/-- Follows `flash-any-image`: any well-formed image is accepted. -/
def runFlashAnyImage (s : State) (minor : Nat) (ctx : Ctx) : Cmd → State × Body
  | .modFlash id (.image i) =>
    match s.modules[id]? with
    | none => (s, .error "not_found")
    | some m =>
      let m' := { m with firmware := some i }
      ({ s with modules := s.modules.set! id m' }, .module (moduleView id m'))
  | c => run s minor ctx c

def stepOf : String → Option (State → Action → State × Out)
  | "reference" => some step
  | "bind-no-duplicate-guard" => some (stepWith runBindNoDuplicateGuard)
  | "delete-names-partial" => some (stepWith runDeleteNamesPartial)
  | "flash-any-image" => some (stepWith runFlashAnyImage)
  | _ => none

end Forge
