---------------------------- MODULE SafeAtomicLock ----------------------------
(*
  Abstract model of safeatomic's cooperative file lock.

  This model uses symbolic, not numeric, process identifiers.  It does not
  model os.getpid(), os.kill(), real filesystem inodes, or network time.
  The host concept is limited to "local" vs. "remote" to capture the key
  invariant: a local process cannot declare a remote lock stale.

  lastRelease records HOW the most recent lock was released.  It is reset
  to Normal when a new lock is acquired (local or remote), so it always
  describes the current or most recent acquisition cycle.

  States modelled
  ---------------
  lock        : Free | HeldLocalLive | HeldLocalDead | HeldRemote | Corrupt
  owner       : NoOwner | LocalPid | RemotePid | UnknownOwner
  lastRelease : None | Normal | Stale | Force
*)

EXTENDS TLC, FiniteSets

CONSTANTS
  \* lock states
  Free, HeldLocalLive, HeldLocalDead, HeldRemote, Corrupt,
  \* owner tokens
  NoOwner, LocalPid, RemotePid, UnknownOwner,
  \* release-mode tokens
  ReleaseNone, ReleaseNormal, ReleaseStale, ReleaseForce

LockStates    == {Free, HeldLocalLive, HeldLocalDead, HeldRemote, Corrupt}
OwnerStates   == {NoOwner, LocalPid, RemotePid, UnknownOwner}
ReleaseStates == {ReleaseNone, ReleaseNormal, ReleaseStale, ReleaseForce}

VARIABLES lock, owner, lastRelease

vars == <<lock, owner, lastRelease>>

(* ------------------------------------------------------------------ *)
(* Initial state                                                        *)
(* ------------------------------------------------------------------ *)

Init ==
  /\ lock        = Free
  /\ owner       = NoOwner
  /\ lastRelease = ReleaseNone

(* ------------------------------------------------------------------ *)
(* Helper: reset release record on new acquisition                     *)
(* ------------------------------------------------------------------ *)

ResetRelease == lastRelease' = ReleaseNone

(* ------------------------------------------------------------------ *)
(* Actions                                                              *)
(* ------------------------------------------------------------------ *)

AcquireLocalLock ==
  /\ lock = Free
  /\ lock'  = HeldLocalLive
  /\ owner' = LocalPid
  /\ ResetRelease

ReleaseLocalLock ==
  /\ lock  = HeldLocalLive
  /\ owner = LocalPid
  /\ lock'        = Free
  /\ owner'       = NoOwner
  /\ lastRelease' = ReleaseNormal

CrashLocalOwner ==
  (* Local owner disappears; lock becomes dead but is not yet released. *)
  /\ lock  = HeldLocalLive
  /\ owner = LocalPid
  /\ lock' = HeldLocalDead
  /\ UNCHANGED <<owner, lastRelease>>

RemoteLockAppears ==
  (* A remote host writes a lock file; resets release record for this cycle. *)
  /\ lock = Free
  /\ lock'  = HeldRemote
  /\ owner' = RemotePid
  /\ ResetRelease

CorruptLock ==
  (* Lock file becomes unparseable (truncated, wrong format…).
     Only a lock that already EXISTS can be corrupted; Free means no
     lock file is present, so corruption cannot apply there.          *)
  /\ lock \in {HeldLocalLive, HeldLocalDead, HeldRemote}
  /\ lock'  = Corrupt
  /\ owner' = UnknownOwner
  /\ UNCHANGED lastRelease

DetectLocalDead ==
  (*
    Deliberate observational action: a local process inspects the lock
    state and confirms that the dead-local PID is gone, without changing
    any variable.  Modelled as pure stuttering so TLC can exercise the
    enabling condition (lock = HeldLocalDead /\ owner = LocalPid)
    without coupling it to a state mutation.  Kept distinct from
    NoOpReadLockState because it carries a stronger precondition that
    documents WHEN such an observation is meaningful.
  *)
  /\ lock  = HeldLocalDead
  /\ owner = LocalPid
  /\ UNCHANGED vars

ReleaseStaleLocal ==
  (* A new local process reclaims a dead-local lock via stale-recovery. *)
  /\ lock  = HeldLocalDead
  /\ owner = LocalPid
  /\ lock'        = Free
  /\ owner'       = NoOwner
  /\ lastRelease' = ReleaseStale

ForceRelease ==
  (* Administrative override: clears any non-Free lock. *)
  /\ lock # Free
  /\ lock'        = Free
  /\ owner'       = NoOwner
  /\ lastRelease' = ReleaseForce

NoOpReadLockState ==
  /\ UNCHANGED vars

Next ==
  \/ AcquireLocalLock
  \/ ReleaseLocalLock
  \/ CrashLocalOwner
  \/ RemoteLockAppears
  \/ CorruptLock
  \/ DetectLocalDead
  \/ ReleaseStaleLocal
  \/ ForceRelease
  \/ NoOpReadLockState

Spec == Init /\ [][Next]_vars

(* ------------------------------------------------------------------ *)
(* Type invariant                                                        *)
(* ------------------------------------------------------------------ *)

TypeInvariant ==
  /\ lock        \in LockStates
  /\ owner       \in OwnerStates
  /\ lastRelease \in ReleaseStates

(* ------------------------------------------------------------------ *)
(* Safety invariants                                                    *)
(* ------------------------------------------------------------------ *)

(*
  While the lock is held live by a local process, stale-recovery and
  force-override have not yet occurred in this acquisition cycle.
*)
AtMostOneLiveLocalOwner ==
  lock = HeldLocalLive =>
    /\ owner = LocalPid
    /\ lastRelease = ReleaseNone

(*
  Stale-recovery produces a Free lock; the protocol never leaves the
  lock in a held state after declaring it stale.
*)
StaleReleaseOnlyWhenStale ==
  lastRelease = ReleaseStale => lock = Free

(*
  Force-release always results in a Free lock.
*)
ForceReleaseIsAdministrativeOverride ==
  lastRelease = ReleaseForce => lock = Free

(*
  A corrupt lock is not eligible for stale-recovery (which requires
  knowing the owner is a dead local PID).  Corrupt locks need ForceRelease.
*)
CorruptLockIsNotStaleRecovery ==
  lock = Corrupt => lastRelease # ReleaseStale

(*
  Remote locks cannot be reclaimed via PID-liveness stale-recovery.
  Only ForceRelease can clear a remote lock.
*)
RemoteLockNotDeclaredPidStale ==
  owner = RemotePid => lastRelease # ReleaseStale

=============================================================================
