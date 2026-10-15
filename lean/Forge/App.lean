import Companion.Cli
import Forge.Model

/-!
The forge app as the generic command line sees it: model, search areas and validation
alphabet. Its properties are left empty; each executable adds its own: `Forge.Main` the
hand-written ones in `Forge.Props` (the feasibility study), and `Forge.CompanionMain` the ones
generated from the constraint ledger (`Forge.Ledger`).
-/

namespace Forge

open Companion

def areas : List (Area State Action) :=
  ["alloc", "unit", "module", "access"].filterMap fun n =>
    (scopeOf n).map fun sc => { name := n, alphabet := sc.alphabet, bound := sc.bound }

def app : App State Action Out Obs :=
  { model, areas, validationAlphabet, properties := [] }

end Forge
