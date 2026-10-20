import Companion.Cli
open Lean
namespace Booking

/-! Model of app/booking.py. Rooms are naturals (the adapter maps `n` to `room-n`); a
booking's id is its position in creation order, which is also the program's id. -/

structure Bk where
  id : Nat
  room : Nat
  start : Nat
  stop : Nat
deriving BEq, Hashable, Repr, ToJson, FromJson

structure State where
  bookings : List Bk := []
  next : Nat := 0
deriving BEq, Hashable, Repr

inductive Action where
  | book (room start stop : Nat)
  | cancel (id : Nat)
  | list
deriving BEq, Repr, ToJson, FromJson

inductive Out where
  | booked (b : Bk)
  | cancelled
  | bookings (bs : List Bk)
  | error (category : String)
deriving BEq, Repr, ToJson, FromJson

structure Obs where
  bookings : List Bk
deriving BEq, Repr, ToJson, FromJson

def observe (s : State) : Obs := { bookings := s.bookings }

def overlaps (b : Bk) (room start stop : Nat) : Bool :=
  b.room == room && b.start < stop && start < b.stop

def step (s : State) : Action → State × Out
  | .book room start stop =>
    if stop ≤ start then (s, .error "invalid_range")
    else if s.bookings.any (overlaps · room start stop) then (s, .error "conflict")
    else
      let b := { id := s.next, room, start, stop }
      ({ bookings := s.bookings ++ [b], next := s.next + 1 }, .booked b)
  | .cancel id =>
    if s.bookings.any (·.id == id) then ({ s with bookings := s.bookings.filter (·.id != id) }, .cancelled)
    else (s, .error "not_found")
  | .list => (s, .bookings s.bookings)

def model : Companion.Model State Action Out Obs := { init := {}, step, observe }

def slots : List (Nat × Nat) := [(0, 2), (1, 3), (2, 4)]

def app : Companion.App State Action Out Obs :=
  { model
    areas := [{ name := "bookings",
                alphabet := (([0, 1].flatMap fun r => slots.map fun (a, b) => Action.book r a b)
                             ++ [0, 1, 2].map .cancel ++ [.list]),
                bound := fun s => s.next ≤ 3 }]
    validationAlphabet := ([0, 1, 2].flatMap fun r => [(0, 2), (1, 3), (2, 4), (3, 5), (2, 2), (3, 1)].map
                             fun (a, b) => Action.book r a b)
                          ++ [0, 1, 2, 7].map .cancel ++ [.list]
    properties := [] }

end Booking
