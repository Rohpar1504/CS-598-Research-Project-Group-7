import Forge.App
import Forge.Props
import Forge.Variants

/-! The feasibility study's executable `forge-model`: the forge app with the hand-written
properties of `Forge.Props`. See `Companion.Cli` for the subcommands. -/

def main (args : List String) : IO UInt32 := ({ Forge.app with properties := Forge.properties, variants := Forge.variants }).main args
