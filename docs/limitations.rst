.. _Known Issues in Magic-Folder:

Known Issues and Limitations
============================

* If a file enters the "conflicted" state there is no way to get it out
  of this state currently (see `Issue 102`_)

* The status WebSocket (see :ref:`status-api`) endpoint produces a lot of output when there
  are lots of files; see `Issue 686`_ for more.

* Unicode filenames are supported on both Linux and Windows, but on
  Linux the local name of a file must be encoded correctly in order
  for it to be uploaded.  The expected encoding is that printed by
  ``python -c "import sys; print sys.getfilesystemencoding()"``.

* On Windows, ``magic-folder run`` may be unresponsive to Ctrl-C (it
  can only be killed using Task Manager or similar). (`#2218`_)

.. _`Issue 102`: https://github.com/LeastAuthority/magic-folder/issues/102
.. _`Issue 686`: https://github.com/LeastAuthority/magic-folder/issues/686
.. _`#1430`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1430
.. _`#1431`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1431
.. _`#1432`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1432
.. _`#1459`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1459
.. _`#1711`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1711
.. _`#2218`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2218
.. _`#2219`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2219
.. _`#2440`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2440
.. _`garbage collection`: https://tahoe-lafs.readthedocs.io/en/latest/garbage-collection.html
