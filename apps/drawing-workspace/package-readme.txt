Heleos drawing workspace — relocatable preview

Keep this application folder separate from your saved project and runtime profile.
You can move or replace the application folder and reopen the same project.

This preview requires an installed Python 3.9+ interpreter (64-bit CPython on
Windows), native Heleos Foundation
CLI, and Poppler pdftotext/pdftoppm with their installed libraries. They are not
included here. No tools or models are downloaded automatically. Configure their
absolute paths using launch.py configure, then use launch.py check before run.
Run with isolated Python startup: python -I -B /path/to/package/launch.py --help
The -I and -B options are required before the launcher path. They isolate imports
and prevent bytecode writes. Use the same installed interpreter for all commands.

The printed WORKSPACE_URL opens the local drawing workspace in your browser.
Keep the launcher running while working; stop with Ctrl+C. Windows shutdown is
forceful: the launcher terminates the application's owned process job. Save your
work before stopping. The next run prints
a new URL. Keep the complete project folder when backing up or moving your work.
Do not store projects, profiles, renderer caches or other files in this package.

package.json records exact application files and approved duct rule records.
Its identity detects accidental changes; it is not a signature or release approval.
Configure a new external profile after changing installed tool bytes or machines.
The profile records file identities, not model approval or native library readiness.

This folder is an installed-runtime preview, not a standalone Mac/Windows installer.
Mac verification and platform-neutral tests do not establish native Windows parity.
Windows startup verifies a noninheritable Job Object and exact child membership
before allowing the application to run. Cleanup covers associated descendants,
including after the main child exits. It depends on CPython's private retained
subprocess.Handle; an unrecognized or closed handle prevents startup. Native
Windows lifecycle and complete application checks remain required before use.
Mechanical categories and representative-project accuracy remain in progress.

Supported circular duct centerlines use three ordered source anchors and verified
scale. Review explicit centerline radius and whole-arc length readings separately.
Conflicts remain exceptions. Source-backed size boundaries can split a supported
arc into measured portions while retaining the original radius and whole-arc
checks. Portions support further splits, reviewed rereads, and group/evidence
correction. Saved portion boundaries remain fixed during ordinary correction.
Moving an existing family boundary, full circles and other curve classes remain
unfinished; the planar accuracy evaluator reports arcs and portions as unscored.
