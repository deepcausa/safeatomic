-------------------------- MODULE SafeAtomicChecksum --------------------------
(*
  Abstract model of safeatomic's checksum-sidecar integrity mechanism.

  This model focuses on two questions:
    1. Does a mismatch between target and checksum sidecar always produce
       verifyResult = Mismatch (never a false Match)?
    2. Does a crash between WriteTargetNew and WriteChecksumNew leave a
       detectable inconsistency?

  verifiedTarget / verifiedChecksum record the exact (read, sidecar) pair
  that was in effect at the moment VerifyChecksum ran.  The invariant
  ChecksumMatchImpliesHashConsistent refers to those snapshot values, not
  to the current live values of target/checksum (which may advance after
  the verify call).

  What this model does NOT prove
  ------------------------------
  - That the checksum sidecar always exists (we model NoChecksum explicitly).
  - That hash collisions cannot occur (WrongHash is a distinct model value).
  - Filesystem durability, fdatasync ordering, or journal behaviour.
  - Python implementation correctness.
*)

EXTENDS TLC

CONSTANTS
  Old, New, Corrupt,                        \* target content states
  NoChecksum, OldHash, NewHash, WrongHash,  \* checksum sidecar states
  NoRead, NotChecked, Match, Mismatch       \* read / verify results

TargetStates   == {Old, New, Corrupt}
ChecksumStates == {NoChecksum, OldHash, NewHash, WrongHash}
ReadStates     == {NoRead, Old, New, Corrupt}
VerifyStates   == {NotChecked, Match, Mismatch}

VARIABLES
  target,               \* current committed target content
  checksum,             \* current checksum sidecar
  lastRead,             \* content seen by the most recent ReadTarget
  verifyResult,         \* outcome of the most recent VerifyChecksum
  verifiedTarget,       \* snapshot of lastRead at time of last VerifyChecksum
  verifiedChecksum,     \* snapshot of checksum  at time of last VerifyChecksum
  writeSuccess,         \* TRUE once WriteTargetNew completed atomically
  checksumWriteSuccess  \* TRUE once WriteChecksumNew completed

vars == <<target, checksum, lastRead, verifyResult,
          verifiedTarget, verifiedChecksum,
          writeSuccess, checksumWriteSuccess>>

(* ------------------------------------------------------------------ *)
(* Initial state                                                        *)
(* ------------------------------------------------------------------ *)

Init ==
  /\ target               = Old
  /\ checksum             = OldHash
  /\ lastRead             = NoRead
  /\ verifyResult         = NotChecked
  /\ verifiedTarget       = NoRead      \* no verification has run yet
  /\ verifiedChecksum     = NoChecksum  \* no verification has run yet
  /\ writeSuccess         = FALSE
  /\ checksumWriteSuccess = FALSE

(* ------------------------------------------------------------------ *)
(* Actions                                                              *)
(* ------------------------------------------------------------------ *)

WriteTargetNew ==
  (* Atomic rename places new content at the target path. *)
  /\ target'               = New
  /\ writeSuccess'         = TRUE
  /\ UNCHANGED <<checksum, lastRead, verifyResult,
                 verifiedTarget, verifiedChecksum, checksumWriteSuccess>>

WriteChecksumNew ==
  (* Sidecar is written after the target rename succeeds. *)
  /\ writeSuccess = TRUE
  /\ checksum'             = NewHash
  /\ checksumWriteSuccess' = TRUE
  /\ UNCHANGED <<target, lastRead, verifyResult,
                 verifiedTarget, verifiedChecksum, writeSuccess>>

CrashAfterTargetBeforeChecksum ==
  (* Process dies after rename but before sidecar update.
     Modelled as a stutter — no further progress from this state if the
     invariant condition holds.  TLC explores what can be verified in
     a state where target=New and checksum is still Old/None.          *)
  /\ writeSuccess         = TRUE
  /\ checksumWriteSuccess = FALSE
  /\ checksum \in {OldHash, NoChecksum}
  /\ UNCHANGED vars

CorruptTarget ==
  (* Bit-rot or partial overwrite corrupts the target file. *)
  /\ target'       = Corrupt
  /\ verifyResult' = NotChecked
  /\ UNCHANGED <<checksum, lastRead, verifiedTarget, verifiedChecksum,
                 writeSuccess, checksumWriteSuccess>>

CorruptChecksum ==
  (* The sidecar file is corrupted or replaced with garbage. *)
  /\ checksum'     = WrongHash
  /\ verifyResult' = NotChecked
  /\ UNCHANGED <<target, lastRead, verifiedTarget, verifiedChecksum,
                 writeSuccess, checksumWriteSuccess>>

ReadTarget ==
  (* Observe the current target content. *)
  /\ lastRead' = target
  /\ UNCHANGED <<target, checksum, verifyResult,
                 verifiedTarget, verifiedChecksum,
                 writeSuccess, checksumWriteSuccess>>

VerifyChecksum ==
  (*
    Compare lastRead against the current sidecar.
    Match iff (lastRead = New AND checksum = NewHash)
           OR (lastRead = Old AND checksum = OldHash).
    Any other pairing (including NoChecksum, WrongHash, Corrupt) is Mismatch.
    Snapshots of lastRead and checksum are captured into verifiedTarget /
    verifiedChecksum so invariants can reason about the pair that was checked.
  *)
  /\ lastRead # NoRead
  /\ verifiedTarget'   = lastRead
  /\ verifiedChecksum' = checksum
  /\ verifyResult' =
       IF \/ (lastRead = New /\ checksum = NewHash)
          \/ (lastRead = Old /\ checksum = OldHash)
       THEN Match
       ELSE Mismatch
  /\ UNCHANGED <<target, checksum, lastRead, writeSuccess, checksumWriteSuccess>>

Next ==
  \/ WriteTargetNew
  \/ WriteChecksumNew
  \/ CrashAfterTargetBeforeChecksum
  \/ CorruptTarget
  \/ CorruptChecksum
  \/ ReadTarget
  \/ VerifyChecksum

Spec == Init /\ [][Next]_vars

(* ------------------------------------------------------------------ *)
(* Type invariant                                                        *)
(* ------------------------------------------------------------------ *)

TypeInvariant ==
  /\ target               \in TargetStates
  /\ checksum             \in ChecksumStates
  /\ lastRead             \in ReadStates
  /\ verifyResult         \in VerifyStates
  /\ verifiedTarget       \in ReadStates
  /\ verifiedChecksum     \in ChecksumStates
  /\ writeSuccess         \in BOOLEAN
  /\ checksumWriteSuccess \in BOOLEAN

(* ------------------------------------------------------------------ *)
(* Safety invariants                                                    *)
(* ------------------------------------------------------------------ *)

(*
  verifyResult = Match iff the snapshot pair (verifiedTarget, verifiedChecksum)
  was a genuinely consistent pair at verify time.  This uses snapshots, not
  the live target/checksum (which may have advanced after the verify).
*)
ChecksumMatchImpliesHashConsistent ==
  verifyResult = Match =>
    \/ (verifiedTarget = New /\ verifiedChecksum = NewHash)
    \/ (verifiedTarget = Old /\ verifiedChecksum = OldHash)

(*
  If the target was observed as Corrupt and verification has run,
  the result must be Mismatch, never Match.
*)
CorruptionDetectedAsMismatch ==
  (verifiedTarget = Corrupt /\ verifyResult # NotChecked) =>
    verifyResult = Mismatch

(*
  WrongHash in the sidecar at the time of verification must never
  produce Match regardless of what was read.
*)
NoFalseMatchForWrongHash ==
  verifiedChecksum = WrongHash => verifyResult # Match

=============================================================================
