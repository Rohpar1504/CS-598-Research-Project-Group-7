import Std.Data.HashSet
import Lean.Data.Json

/-!
Machinery shared by every application model: replay, exploration and property
checking. An application supplies a `Model`; nothing here depends on the application.
-/

namespace Companion

open Lean

/-- An executable model of an application.

`step` is total: a rejected request is an output (an error), not a failure of the model.
`observe` is what the application exposes through its read interface. Properties are
stated over observations, so one predicate can be evaluated on the model and on the real
application alike. -/
structure Model (σ α ο θ : Type) where
  init    : σ
  step    : σ → α → σ × ο
  observe : σ → θ
  /-- Exploration only visits states for which this holds (the small scope). -/
  inScope : σ → Bool := fun _ => true

/-- One observed transition. Every property is a predicate on transitions; a state
invariant `P` is the transition property `P after`. -/
structure Transition (α ο θ : Type) where
  before : θ
  action : α
  out    : ο
  after  : θ
deriving ToJson, FromJson

structure Property (α ο θ : Type) where
  name  : String
  holds : Transition α ο θ → Bool

/-- Run `trace` from the initial state. Returns the initial observation and, for each
step, the output and the observation after it. -/
def replay (m : Model σ α ο θ) (trace : List α) : θ × List (ο × θ) := Id.run do
  let mut s := m.init
  let mut steps := #[]
  for a in trace do
    let (s', o) := m.step s a
    steps := steps.push (o, m.observe s')
    s := s'
  return (m.observe m.init, steps.toList)

structure SearchResult (α : Type) where
  property       : String
  counterexample : Option (List α)
  statesExplored : Nat
deriving ToJson

/-- Breadth-first search over the states reachable within `depth` actions drawn from
`alphabet`. Returns the shortest trace whose last transition violates `p`.
Traces are kept reversed so that they share structure. -/
def bfs [BEq σ] [Hashable σ] (m : Model σ α ο θ) (alphabet : List α) (p : Property α ο θ)
    (depth : Nat) : SearchResult α := Id.run do
  let mut frontier : Array (σ × List α) := #[(m.init, [])]
  let mut seen : Std.HashSet σ := ({} : Std.HashSet σ).insert m.init
  for _ in [0:depth] do
    let mut next := #[]
    for (s, tr) in frontier do
      let before := m.observe s
      for a in alphabet do
        let (s', o) := m.step s a
        if !m.inScope s' then continue
        if !p.holds ⟨before, a, o, m.observe s'⟩ then
          return { property := p.name, counterexample := some (a :: tr).reverse,
                   statesExplored := seen.size }
        if !seen.contains s' then
          seen := seen.insert s'
          next := next.push (s', a :: tr)
    frontier := next
  return { property := p.name, counterexample := none, statesExplored := seen.size }

/-- One shortest trace to each state reachable within `depth` actions, thinned to at most
`limit` traces spread evenly over the search order. Used to pick traces for validation. -/
def paths [BEq σ] [Hashable σ] (m : Model σ α ο θ) (alphabet : List α) (depth limit : Nat) :
    Array (List α) := Id.run do
  let mut frontier : Array (σ × List α) := #[(m.init, [])]
  let mut seen : Std.HashSet σ := ({} : Std.HashSet σ).insert m.init
  let mut found : Array (List α) := #[]
  for _ in [0:depth] do
    let mut next := #[]
    for (s, tr) in frontier do
      for a in alphabet do
        let (s', _) := m.step s a
        if m.inScope s' && !seen.contains s' then
          seen := seen.insert s'
          next := next.push (s', a :: tr)
          found := found.push (a :: tr).reverse
    frontier := next
  if found.size ≤ limit then return found
  let stride := found.size / limit
  return (Array.range limit).map fun i => found[i * stride]!

end Companion
