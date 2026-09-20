# RM-0008 Windows real-identity barrier

This directory contains the environment and oracle calibration required before
any RM-0008 R5-R7 product correction is allowed.

The dedicated Windows calibration step is part of the existing
`portable-contract-store` matrix job, so the established Windows check cannot
pass without it. It creates two temporary standard local accounts. Its
controller validates each suspended child token before the child can run, then
the child exercises actual Win32 file access. Passwords stay inside mutable
controller buffers and are never passed through arguments, environment
variables, files, logs, or artifacts.

The structural oracle deliberately does not import Metnos runtime code. It
reads owner, DACL protection, ACE order, SID, flags, and masks through Win32
security APIs. Known-invalid fixtures prove that the oracle rejects a wrong
owner, an extra confidential reader, and a writable service profile. The job
fails rather than skipping if account creation, logon, token isolation, NTFS
ACL support, privilege use, cleanup, or any expected access result is absent.

This calibration does not certify the product implementation. Future product
acceptance tests may use the calibrated controller and oracle only after this
job is green on the public `windows-2022` runner.

`rm0008_increment_2a_windows_diagnostics.py` is a separate historical
reproducer. The local pytest hook executes it only after a successful identity
calibration in a manually dispatched workflow; normal pushes do not run it and
it adds no pytest node to the mandatory collection. Success means that the
frozen prototype defects R5-R7 were observed, not that the product is
conformant. The mandatory acceptance tests will assert the opposite invariants
after the diagnostic phase.
