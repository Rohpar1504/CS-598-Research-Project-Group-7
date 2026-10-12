import Forge.Props
import Forge.Variants

/-!
Command-line interface for the forge model. All input and output is JSON.

  forge-model alphabet <AREA|validation>    an action alphabet
  forge-model paths <AREA> <depth> <limit>  shortest traces to reachable states
  forge-model replay [VARIANT]              stdin: list of actions
                                                 stdout: {initial, steps: [{out, obs}]}
  forge-model check <AREA> <depth> [VARIANT]
                                                 search every property within an area
  forge-model eval                          stdin: list of transitions
                                                 stdout: names of violated properties per transition

AREA is alloc, unit, module or access. VARIANT selects a model that follows a seeded-bug
version of forge (default: reference).
-/

open Lean Forge Companion

def readStdinJson : IO Json := do
  IO.ofExcept (Json.parse (← (← IO.getStdin).readToEnd))

def emit (j : Json) : IO Unit := IO.println j.compress

def main (args : List String) : IO UInt32 := do
  match args with
  | ["alphabet", "validation"] => emit (toJson validationAlphabet)
  | ["alphabet", area] =>
    let some sc := scopeOf area | throw <| IO.userError s!"unknown area {area}"
    emit (toJson sc.alphabet)
  | ["paths", area, depth, limit] =>
    let some sc := scopeOf area | throw <| IO.userError s!"unknown area {area}"
    emit (toJson (paths (model sc.bound) sc.alphabet depth.toNat! limit.toNat!))
  | "replay" :: rest =>
    let some st := stepOf (rest.headD "reference") | throw <| IO.userError "unknown variant"
    let trace ← IO.ofExcept (fromJson? (← readStdinJson) : Except String (List Action))
    let (initial, steps) := replay { model with step := st } trace
    emit (Json.mkObj [("initial", toJson initial),
      ("steps", toJson (steps.map fun (o, obs) => Json.mkObj [("out", toJson o), ("obs", toJson obs)]))])
  | "check" :: area :: depth :: rest =>
    let some sc := scopeOf area | throw <| IO.userError s!"unknown area {area}"
    let some st := stepOf (rest.headD "reference") | throw <| IO.userError "unknown variant"
    let m := { model sc.bound with step := st }
    emit (toJson (properties.map fun p => bfs m sc.alphabet p depth.toNat!))
  | ["eval"] =>
    let ts ← IO.ofExcept (fromJson? (← readStdinJson) : Except String (List (Transition Action Out Obs)))
    emit (toJson (ts.map fun t => (properties.filter (!·.holds t)).map (·.name)))
  | _ =>
    IO.eprintln "usage: forge-model (alphabet AREA | paths AREA DEPTH LIMIT | replay [VARIANT] | check AREA DEPTH [VARIANT] | eval)"
    return 2
  return 0
