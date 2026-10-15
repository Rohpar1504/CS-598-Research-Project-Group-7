import Companion.Core

/-!
A generic JSON command line for an application model. An application declares an `App`
(its model, search areas, validation alphabet, properties and bug-following variants) and
gets an executable with these subcommands:

  areas                               the search areas
  properties                          the names of the properties
  alphabet <AREA|validation>          an action alphabet
  paths <AREA> <depth> <limit>        shortest traces to reachable states
  replay [VARIANT]                    stdin: list of actions; stdout: {initial, steps: [{out, obs}]}
  replay-many [VARIANT]               stdin: list of traces; stdout: one replay result per trace
  check <AREA> <depth> [VARIANT]      search every property within an area
  eval                                stdin: list of transitions; stdout: violated property names per transition
-/

namespace Companion

open Lean

/-- A search area: an action alphabet and a bound on the states explored. -/
structure Area (σ α : Type) where
  name     : String
  alphabet : List α
  bound    : σ → Bool

structure App (σ α ο θ : Type) where
  model              : Model σ α ο θ
  areas              : List (Area σ α)
  validationAlphabet : List α
  properties         : List (Property α ο θ)
  /-- Models that follow other versions of the code, by name. -/
  variants           : List (String × (σ → α → σ × ο)) := []

def readStdinJson : IO Json := do
  IO.ofExcept (Json.parse (← (← IO.getStdin).readToEnd))

def emit (j : Json) : IO Unit := IO.println j.compress

/-- The replay of a trace as JSON: `{initial, steps: [{out, obs}]}`. -/
def replayJson [ToJson ο] [ToJson θ] (m : Model σ α ο θ) (st : σ → α → σ × ο) (trace : List α) : Json :=
  let (initial, steps) := replay { m with step := st } trace
  Json.mkObj [("initial", toJson initial),
    ("steps", toJson (steps.map fun (p : ο × θ) => Json.mkObj [("out", toJson p.1), ("obs", toJson p.2)]))]

def App.main [BEq σ] [Hashable σ] [ToJson α] [FromJson α] [ToJson ο] [FromJson ο] [ToJson θ]
    [FromJson θ] (app : App σ α ο θ) (args : List String) : IO UInt32 := do
  let stepOf (v : String) : IO (σ → α → σ × ο) :=
    if v == "reference" then pure app.model.step
    else match app.variants.lookup v with
      | some st => pure st
      | none => throw <| IO.userError s!"unknown variant {v}"
  let areaOf (a : String) : IO (Area σ α) :=
    match app.areas.find? (·.name == a) with
    | some ar => pure ar
    | none => throw <| IO.userError s!"unknown area {a}"
  match args with
  | ["areas"] => emit (toJson (app.areas.map (·.name)))
  | ["properties"] => emit (toJson (app.properties.map (·.name)))
  | ["alphabet", "validation"] => emit (toJson app.validationAlphabet)
  | ["alphabet", a] => emit (toJson (← areaOf a).alphabet)
  | ["paths", a, depth, limit] =>
    let ar ← areaOf a
    emit (toJson (paths { app.model with inScope := ar.bound } ar.alphabet depth.toNat! limit.toNat!))
  | "replay" :: rest =>
    let st ← stepOf (rest.headD "reference")
    let trace ← IO.ofExcept (fromJson? (← readStdinJson) : Except String (List α))
    emit (replayJson app.model st trace)
  | "replay-many" :: rest =>
    let st ← stepOf (rest.headD "reference")
    let traces ← IO.ofExcept (fromJson? (← readStdinJson) : Except String (List (List α)))
    emit (toJson (traces.map (replayJson app.model st)))
  | "check" :: a :: depth :: rest =>
    let ar ← areaOf a
    let m := { app.model with step := ← stepOf (rest.headD "reference"), inScope := ar.bound }
    emit (toJson (app.properties.map fun p => bfs m ar.alphabet p depth.toNat!))
  | ["eval"] =>
    let ts ← IO.ofExcept (fromJson? (← readStdinJson) : Except String (List (Transition α ο θ)))
    emit (toJson (ts.map fun t => (app.properties.filter (!·.holds t)).map (·.name)))
  | _ =>
    IO.eprintln ("usage: (areas | properties | alphabet AREA | paths AREA DEPTH LIMIT | " ++
      "replay [VARIANT] | replay-many [VARIANT] | check AREA DEPTH [VARIANT] | eval)")
    return 2
  return 0

end Companion
