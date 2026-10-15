import Forge.App
import Forge.Ledger

/-! The companion's executable `forge-companion`: the forge app with the properties
generated from the constraint ledger (`Forge.Ledger`, written by companion/ledger.py).
See `Companion.Cli` for the subcommands. -/

def main (args : List String) : IO UInt32 := ({ Forge.app with properties := Forge.ledgerProperties }).main args
