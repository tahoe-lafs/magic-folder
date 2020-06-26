.. _Known Issues in Magic-Folder:

Known Issues and Limitations
============================

* The only way to determine whether uploads have failed is to look at
  the 'Operational Statistics' page linked from the Welcome page.
  This only shows a count of failures, not the names of files.
  Uploads are never retried.

* The Magic Folder frontend performs its uploads sequentially (i.e. it
  waits until each upload is finished before starting the next), even
  when there would be enough memory and bandwidth to efficiently
  perform them in parallel.  A Magic Folder upload can occur in
  parallel with an upload by a different frontend, though. (`#1459`_)

* On Linux, if there are a large number of near-simultaneous file
  creation or change events (greater than the number specified in the
  file ``/proc/sys/fs/inotify/max_queued_events``), it is possible
  that some events could be missed.  This is fairly unlikely under
  normal circumstances, because the default value of
  ``max_queued_events`` in most Linux distributions is 16384, and
  events are removed from this queue immediately without waiting for
  the corresponding upload to complete. (`#1430`_)

* The Windows implementation might also occasionally miss file
  creation or change events, due to limitations of the underlying
  Windows API (ReadDirectoryChangesW).  We do not know how likely or
  unlikely this is. (`#1431`_)

* Some filesystems may not support the necessary change notifications.
  It is recommended for the local directory to be on a directly
  attached disk-based filesystem, not a network filesystem or one
  provided by a virtual machine.

* If a file in the upload directory is changed (actually relinked to a
  new file), then the old file is still present on the grid, and any
  other caps to it will remain valid.  Eventually it will be possible
  to use `garbage collection`_ to reclaim the space used by these
  files; however currently they are retained indefinitely. (`#2440`_)

* Unicode filenames are supported on both Linux and Windows, but on
  Linux the local name of a file must be encoded correctly in order
  for it to be uploaded.  The expected encoding is that printed by
  ``python -c "import sys; print sys.getfilesystemencoding()"``.

* On Windows, local directories with non-ASCII names are not currently
  working. (`#2219`_)

* On Windows, ``magic-folder run`` may be unresponsive to Ctrl-C (it
  can only be killed using Task Manager or similar). (`#2218`_)

.. _`#1430`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1430
.. _`#1431`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1431
.. _`#1432`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1432
.. _`#1459`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1459
.. _`#1711`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1711
.. _`#2218`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2218
.. _`#2219`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2219
.. _`#2440`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2440
.. _`garbage collection`: https://tahoe-lafs.readthedocs.io/en/latest/garbage-collection.html
