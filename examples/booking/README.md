# Example: room bookings

A 60-line CLI (`app/booking.py`: `book`, `cancel`, `list`). The developer states two
constraints; in turn 2 the coding agent adds `move` (`turn2/booking.py`) without the overlap
check.

```
examples/booking/run.sh        # about 4 min, $0.05 with Claude Haiku 5.5
```

It runs, in a copy of the app:

```
companion init booking.py
companion synthesize
companion tell "no two bookings in the same room may overlap, and if a request is refused nothing should change"
companion check
companion tell "let people move a booking to another time"
cp turn2/booking.py booking.py          # the coding agent's edit
companion check
```

Output of a run on Oct 20 (the agent's steps shortened):

```
The LLM (claude-haiku-5-5) is writing the model of booking.py:
  runs cat booking.py
  writes lean/Booking.lean
  writes adapter.py
  runs validate.py --quick
    → validation: 0 of 166 traces mismatch
  runs validate.py
    → validation: 0 of 452 traces mismatch
  done (15 turns, $0.03)

The model matches the program on 452 traces (1.9 min, $0.03).
added c1: No two live bookings in the same room overlap.
added c2: When a request is refused, the observable state is unchanged.
Turn 1 · booking.py
The model matches the code on 452 traces (2.3 s).

✓ c1  No two live bookings in the same room overlap.
✓ c2  When a request is refused, the observable state is unchanged.

2 checked.
No constraints in that message.
The LLM (claude-haiku-5-5) is updating the model of booking.py:
  edits lean/Booking.lean
  edits adapter.py
  runs validate.py
    → validation: 0 of 720 traces mismatch
  done (17 turns, $0.02)
Turn 2 · booking.py
The model matches the code on 720 traces (69.2 s).
The LLM updated the model to follow the code (1 round, 65 s, $0.02).

✗ c1  No two live bookings in the same room overlap.
     Violated (regression: stated in turn 1). Reproduced on the real program:
       python3 booking.py --data-dir data book room-0 0 1
       python3 booking.py --data-dir data book room-0 1 2
       python3 booking.py --data-dir data move 0 1 2
✓ c2  When a request is refused, the observable state is unchanged.

1 checked, 1 violated.
```

`reference/` holds a hand-written model, adapter and ledger for the same app, for comparison.
