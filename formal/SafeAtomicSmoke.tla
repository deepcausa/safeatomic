--------------------------- MODULE SafeAtomicSmoke ---------------------------

EXTENDS TLC

CONSTANTS Old, New, Partial, NoneVal

VARIABLES target, tmp, success, readResult

Values == {Old, New, Partial, NoneVal}

Init ==
  /\ target = Old
  /\ tmp = NoneVal
  /\ success = FALSE
  /\ readResult = NoneVal

WriteTmpPartial ==
  /\ tmp = NoneVal
  /\ tmp' = Partial
  /\ UNCHANGED <<target, success, readResult>>

WriteTmpComplete ==
  /\ tmp \in {NoneVal, Partial}
  /\ tmp' = New
  /\ UNCHANGED <<target, success, readResult>>

ReplaceTarget ==
  /\ tmp = New
  /\ target' = New
  /\ tmp' = NoneVal
  /\ success' = TRUE
  /\ UNCHANGED readResult

Read ==
  /\ readResult' = target
  /\ UNCHANGED <<target, tmp, success>>

Crash ==
  /\ tmp' = NoneVal
  /\ UNCHANGED <<target, success, readResult>>

Next ==
  \/ WriteTmpPartial
  \/ WriteTmpComplete
  \/ ReplaceTarget
  \/ Read
  \/ Crash

Spec ==
  Init /\ [][Next]_<<target, tmp, success, readResult>>

TypeInvariant ==
  /\ target \in Values
  /\ tmp \in Values
  /\ success \in BOOLEAN
  /\ readResult \in Values

NoPartialTarget ==
  target # Partial

ReadReturnsCommittedVersion ==
  readResult \in {NoneVal, Old, New}

=============================================================================
