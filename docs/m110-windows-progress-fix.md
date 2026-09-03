# M110 patch — Windows-safe research progress

## Incident and scope

On Windows / CPython 3.11, the M107–M110 installation's full suite reached the
spawned campaign test and failed while replacing `queue.json` with `WinError 5`.
The installer correctly refused to launch Dusty. The same release had passed the
Windows CI matrix: one successful run did not exclude an intermittent sharing race.

The worker repeatedly replaced the same progress file that the coordinator opened
to display progress. Windows can reject replacement while a reader holds a handle
without delete sharing. Microsoft's [CreateFile sharing-mode documentation](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilea)
explains that delete sharing also covers rename. This is a concrete conflict in our
old design, consistent with the traceback; the traceback alone cannot exclude a
separate permissions or third-party file-lock problem on the user's PC.

## Correction

- Publish `queue-000.json`, `queue-001.json`, and so on to **new destinations**.
  Write, flush, fsync and close the temporary before publication. Readers inspect
  only complete numbered snapshots, not temporary files or a mutable latest pointer.
- The worker remains the single writer. Only after joining it can the coordinator
  append a cancellation/failure/timeout seal. Prior snapshots and completed case
  hashes stay intact. Sealing an already sealed queue does not append another file.
- A normal 30-case campaign creates 61 snapshots; the hard limit is 64, allowing
  failure/sealing records while keeping storage bounded. There is no pruning during
  a run and no implicit resume, retry, selection or optimization.
- Progress remains advisory and request-hash-bound. Even `30/30` cannot report
  completion without the existing worker-exit and full result/artifact hash checks.
- A genuine write failure still propagates. No administrator privileges, permission
  changes, antivirus exclusions, swallowed write errors or permission retries are
  introduced. An unreadable progress snapshot cannot authorize successful results.

Final `report.json`, `result.json`, case files, model contracts, cost assumptions,
trade calculations, account/risk gates and the read-only MT5 boundary are unchanged.
Legacy `queue.json` files are preserved but not used as progress for new runs. Old
reports remain readable through their existing result hashes. No old prospective
receipt or installation is migrated, rebound, evaluated or consumed.

## Regression checks

The dedicated `test_research_progress.py` CI gate covers immutable destinations,
held readers, publication visibility, interrupted temporaries, corrupt latest data,
propagated write failure, bounded retention, and idempotent terminal sealing.
A spawned writer publishes updates while the parent deliberately holds **every**
previous progress file open. This removes the timing lottery from the regression.

The Windows-only negative control first proves replacement of the held old file
fails with a sharing/access error, then proves the new publication succeeds under
the same open handle. Linux skips that Windows-specific check explicitly. The
existing real spawned campaign, artifact integrity, cancellation-race and UI tests
also remain in the full suite. These are software checks, not trading certification.

The first patch CI run also exposed a test-only timing assumption: the fake-worker
cancellation test used a one-second real timeout during disk setup. That clock is
now controlled and advanced explicitly for the timeout case. The real worker and
its production timeout are unchanged; the test no longer depends on disk speed.

## Installation boundary

Use the separate `DustyDragon-M105` development folder and its existing virtual
environment. Close Dusty, require a clean worktree, switch to the exact tested patch
commit, and run the full suite before opening it. No dependency reinstall is needed.
Keep the original `DustyDragon-M100` prospective installation untouched. A failed
check must stop launch; preserve the error instead of changing Windows permissions.
