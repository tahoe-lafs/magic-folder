
# GridSync requirements

"Using the list from #498, what magic-folder interfaces are required to let GridSync do its job?"

- magic-folder needs to generally function. For sure this implies at least:

    - fix version-conflict install errors (so "magic-folder" can run) (#303)

    - fix warnings emitted to stdout/err (avoid clobbering json) (#305)

    - fix broken CI (both the above required, plus maybe some Nix work)

    - fix known, relevant bugs already in the issue-tracker (TODO:
      figure out which issues are relevant for sure and tag them into
      the `private-storage` milestone)

    - implement Downloader (see the 128.downloader-2021 branch which
      has a document describing in some detail functionality that
      doesn't yet exit).

    - included in above is the actual "sync downloaded changes to the
      file-system" piece too

    - implement Conflicts:
       - a way to determine when there is a conflict (likely "on filesystem")
       - some API to tell magic-folder how to resolve the conflict ("take theirs" or "take mine")
       - upload a two-parent Snapshot when a Conflict is resolved

    - there may of course be additional functionality that GridSync needs

- GridSync needs to create and run a magic-folder instance

    - create a fresh new-magic-folder (done)

    magic-folder --config ./alice init --listen-endpoint tcp:4444:interface=localhost --node-directory ~/work/leastauthority/src/tahoe-lafs/testgrid/alice/

    - migrate an old-style (tahoe 1.14.0 and earlier) magic-folder (done)

    magic-folder --config ./alice migrate --author meejah --listen-endpoint tcp:4444:interface=localhost --node-directory ~/work/leastauthority/src/tahoe-lafs/testgrid/alice/

    - to run an existing magic-folder instance (done)

    magic-folder --config ./alice run


- GridSync will want relevant logs in case of problems. These it can
  get by collecting stdout and stderr from the ``magic-folder run``
  subprocess. It may also be useful to collect the Eliot log;
  currently there are no command-line options to specify where this
  goes, however.


- GridSync needs some insights into what magic-folder is doing. This
  implies some kind of status API. A real-time WebSocket streaming API
  has been mentioned as being good. Needs more specifications from
  GridSync:

    - what information?

    - how shall we encode it?

    - is it one-way, or bi-directional? (e.g. might GridSync want to
      ask questions instead of just getting things when magic-folder
      feels like it? might GridSync want to change how much / which
      information it gets?)
