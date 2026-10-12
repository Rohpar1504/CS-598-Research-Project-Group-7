import Forge.Props

/-!
Proofs about the forge model. Each holds for every state and every action, so for
any number of resources and sequences of any length, not only the small search scopes.
-/

namespace Forge

/-- "Blueprint create and delete never succeed in system scope." -/
theorem system_scope_forbidden (s : State) (c : Cmd) (ctx : Ctx)
    (hc : c.isBlueprintWrite = true) (hs : ctx.scope = .system) :
    (step s { cmd := c, ctx }).2.body.isError = true := by
  cases c <;> simp [Cmd.isBlueprintWrite] at hc <;>
    (simp only [step]; split <;> simp_all [run, accessError, Body.isError])

/-- "With an access rule, only an admin can create or delete blueprints." -/
theorem admin_required (s : State) (c : Cmd) (ctx : Ctx)
    (hc : c.isBlueprintWrite = true) (hr : ctx.rules = true) (hrole : ctx.role ≠ some .admin) :
    (step s { cmd := c, ctx }).2.body.isError = true := by
  cases c <;> simp [Cmd.isBlueprintWrite] at hc <;>
    (simp only [step]; split <;> (try split) <;> simp_all [run, accessError, Body.isError])

/-- "A bind that adds to an assignment succeeds only if the assignment has no bound
allocations." -/
theorem one_bind_per_assignment (s : State) (a : Nat) (ops : List (Nat × Op)) (t : TenantArg)
    (ctx : Ctx) (hok : (step s { cmd := .alBind a ops t, ctx }).2.body = .accepted)
    (hadd : ops.any (·.2 == .add) = true) :
    s.allocations.any (fun al => al.bound && al.assignment == some a) = false := by
  simp only [step] at hok
  split at hok
  · simp at hok
  · split at hok
    · simp at hok
    · simp only [run] at hok
      repeat' split at hok
      all_goals simp_all

/-- Every allocation is bound exactly when it has an assignment. -/
def BoundIffAssigned (s : State) : Prop :=
  ∀ al ∈ s.allocations, al.bound = al.assignment.isSome

theorem bound_iff_assigned_preserved (s : State) (act : Action) (h : BoundIffAssigned s) :
    BoundIffAssigned (step s act).1 := by
  obtain ⟨c, ctx⟩ := act
  unfold BoundIffAssigned at *
  simp only [step]
  split
  · exact h
  · split
    · exact h
    · cases c <;> simp only [run] <;> (try split) <;> (try split) <;> (try split) <;> (try split) <;>
        (try split) <;> (try split) <;> simp_all [expand, bindOps] <;> grind

/-- A command that answers with an error leaves the state unchanged. -/
theorem run_rejected_changes_nothing (s : State) (minor : Nat) (ctx : Ctx) (c : Cmd) (e : String)
    (h : (run s minor ctx c).2 = .error e) : (run s minor ctx c).1 = s := by
  cases c <;> simp only [run] at h ⊢ <;> (repeat' split) <;> simp_all
  all_goals (split at h <;> simp_all)

/-- "A rejected request changes nothing", for every action. -/
theorem rejected_changes_nothing (s : State) (act : Action) (e : String)
    (h : (step s act).2.body = .error e) : (step s act).1 = s := by
  simp only [step] at h ⊢
  split
  · rfl
  · split
    · rfl
    · split at h <;> (try split at h) <;> simp_all <;>
        exact run_rejected_changes_nothing s _ act.ctx act.cmd e (by simp_all)

/-- No module has two tags with the same key. -/
def TagKeysUnique (s : State) : Prop := (s.tags.map fun t => (t.module, t.key)).Nodup

theorem tag_keys_unique_preserved (s : State) (act : Action) (h : TagKeysUnique s) :
    TagKeysUnique (step s act).1 := by
  obtain ⟨c, ctx⟩ := act
  unfold TagKeysUnique at *
  simp only [step]
  split
  · exact h
  · split
    · exact h
    · cases c <;> simp only [run] <;> (repeat' split) <;> simp_all
      · -- tag create: the new (module, key) pair is not yet present
        rw [List.nodup_append]
        refine ⟨h, by simp, ?_⟩
        intro p hp q hq; simp at hq; subst hq; grind
      · -- tag delete: removing tags keeps keys distinct
        exact h.sublist ((List.filter_sublist).map _)

end Forge
