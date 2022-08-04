.. -*- coding: utf-8-with-signature -*-

====================================
User-Visible Changes in Magic Folder
====================================

.. towncrier start line
Magic_Folder 22.8.0 (2022-08-04)
''''''''''''''''''''''''''''''''

No significant changes.


Magic_Folder 22.8.0 (2022-08-04)
''''''''''''''''''''''''''''''''

Bug Fixes
---------

- Stashed file-paths are unique even with same capability (`#662 <https://github.com/LeastAuthority/magic-folder/issues/662>`_)


Magic_Folder 22.5.0 (2022-05-13)
''''''''''''''''''''''''''''''''

Features
--------

- A pid-file is now written

  If a previous instance is running _and_ appears to be an actual
  magic-folder process, it is killed. Otherwise, magic-folder will
  refuse to start. (`#644 <https://github.com/LeastAuthority/magic-folder/issues/644>`_)


Misc/Other
----------

- `#656 <https://github.com/LeastAuthority/magic-folder/issues/656>`_


Magic_Folder 22.5.0 (2022-05-13)
''''''''''''''''''''''''''''''''

Features
--------

- A pid-file is now written

  If a previous instance is running _and_ appears to be an actual
  magic-folder process, it is killed. Otherwise, magic-folder will
  refuse to start. (`#644 <https://github.com/LeastAuthority/magic-folder/issues/644>`_)


Misc/Other
----------

- `#656 <https://github.com/LeastAuthority/magic-folder/issues/656>`_


Magic_Folder 22.2.1 (2022-02-23)
''''''''''''''''''''''''''''''''

Features
--------

- The status API now shows last-scan and last-poll timestamps (`#646 <https://github.com/LeastAuthority/magic-folder/issues/646>`_)


Misc/Other
----------

- `#642 <https://github.com/LeastAuthority/magic-folder/issues/642>`_


Magic_Folder 22.2.0 (2022-02-14)
''''''''''''''''''''''''''''''''

Features
--------

- Check "connected enough" constantly, not just at startup.

  Further, refuse to do "mutable" operations if our Tahoe-LAFS client is not
  connected to a "happy" number of servers. (`#543 <https://github.com/LeastAuthority/magic-folder/issues/543>`_)


Bug Fixes
---------

- Use Cooperator to copy (potentially large) files (`#196 <https://github.com/LeastAuthority/magic-folder/issues/196>`_)
- Further avoid overwriting local changes (`#454 <https://github.com/LeastAuthority/magic-folder/issues/454>`_)
- "magic-folder status" gives better error when service not running (`#616 <https://github.com/LeastAuthority/magic-folder/issues/616>`_)
- Update several dependencies (`#636 <https://github.com/LeastAuthority/magic-folder/issues/636>`_)


Misc/Other
----------

- `#173 <https://github.com/LeastAuthority/magic-folder/issues/173>`_, `#386 <https://github.com/LeastAuthority/magic-folder/issues/386>`_, `#466 <https://github.com/LeastAuthority/magic-folder/issues/466>`_, `#620 <https://github.com/LeastAuthority/magic-folder/issues/620>`_, `#623 <https://github.com/LeastAuthority/magic-folder/issues/623>`_


Magic_Folder 22.1.0 (2022-01-28)
''''''''''''''''''''''''''''''''

Backwards Incompatible Changes
------------------------------

- --web-port is now a required option with no default (`#81 <https://github.com/LeastAuthority/magic-folder/issues/81>`_)


Features
--------

- magic-folder exits with error if it can't listen (`#67 <https://github.com/LeastAuthority/magic-folder/issues/67>`_)
- Deleting a file uploads a deletion snapshot (`#105 <https://github.com/LeastAuthority/magic-folder/issues/105>`_)
- Integration tests are run against multiple Tahoe versions (`#120 <https://github.com/LeastAuthority/magic-folder/issues/120>`_)
- Automated scanning for local changes (`#138 <https://github.com/LeastAuthority/magic-folder/issues/138>`_)
- Create LocalSnapshot instances. LocalSnapshots are an intermediate snapshot representation that is used to maintain history even when the user modifies files while offline. (`#139 <https://github.com/LeastAuthority/magic-folder/issues/139>`_)
- LocalSnapshots are persisted into the disk to preserve history even if the computer is offline. During startup, magic-folder would look for these persisted LocalSnapshots and try to commit them into the grid. (`#140 <https://github.com/LeastAuthority/magic-folder/issues/140>`_)
- Magic-Folder now exposes a bearer-token-authorized HTTP API hierarchy beneath ``/v1``. (`#198 <https://github.com/LeastAuthority/magic-folder/issues/198>`_)
- Magic-Folder now exposes an HTTP API endpoint, ``/v1/magic-folder``, which can be used to list the managed Magic Folders. (`#205 <https://github.com/LeastAuthority/magic-folder/issues/205>`_)
- Magic-Folder now exposes an HTTP API for creating a new local snapshot of a file. (`#266 <https://github.com/LeastAuthority/magic-folder/issues/266>`_)
- Add a "magic-folder-api add-snapshot" command (`#309 <https://github.com/LeastAuthority/magic-folder/issues/309>`_)
- The development process is documented. (`#322 <https://github.com/LeastAuthority/magic-folder/issues/322>`_)
- Add a "magic-folder-api dump-state" command (`#325 <https://github.com/LeastAuthority/magic-folder/issues/325>`_)
- There is now an HTTP API to add and list new participants, along with corresponding magic-folder-api subcommands (`#327 <https://github.com/LeastAuthority/magic-folder/issues/327>`_)
- Add real-time WebSocket status update endpoint (`#335 <https://github.com/LeastAuthority/magic-folder/issues/335>`_)
- Export api_client_endpoint to config dir (`#339 <https://github.com/LeastAuthority/magic-folder/issues/339>`_)
- Add 'magic-folder-api monitor' command (`#351 <https://github.com/LeastAuthority/magic-folder/issues/351>`_)
- Add a '@metadata' entry to Collective and Personal DMDs (`#420 <https://github.com/LeastAuthority/magic-folder/issues/420>`_)
- More status information emitted. (`#440 <https://github.com/LeastAuthority/magic-folder/issues/440>`_)
- Report errors via /status API (`#481 <https://github.com/LeastAuthority/magic-folder/issues/481>`_)
- Include "last-updated" time in file-status endpoint (`#501 <https://github.com/LeastAuthority/magic-folder/issues/501>`_)
- If the HTTP API listens on port 0, the actual port is reported (`#516 <https://github.com/LeastAuthority/magic-folder/issues/516>`_)
- API to return tahoe object-sizes (`#524 <https://github.com/LeastAuthority/magic-folder/issues/524>`_)
- A spec for conflicts APIs exists (`#537 <https://github.com/LeastAuthority/magic-folder/issues/537>`_)
- Add an explicit 'conflicts' API (`#538 <https://github.com/LeastAuthority/magic-folder/issues/538>`_)
- Add a `magic-folder status` command (`#557 <https://github.com/LeastAuthority/magic-folder/issues/557>`_)
- test against Tahoe 1.16.x (`#564 <https://github.com/LeastAuthority/magic-folder/issues/564>`_)
- Added a .../poll-remote endpoint (and rename /scan to /scan-local) (`#572 <https://github.com/LeastAuthority/magic-folder/issues/572>`_)
- Output "cuvner report" after unit-tests (`#620 <https://github.com/LeastAuthority/magic-folder/issues/620>`_)


Bug Fixes
---------

- The "treq" library is now required (`#139 <https://github.com/LeastAuthority/magic-folder/issues/139>`_)
- Sub-commands no longer accept the --basedir option; use --node-directory instead (`#145 <https://github.com/LeastAuthority/magic-folder/issues/145>`_)
- Internal functions sign_snapshot() and write_snapshot_to_tahoe() support upload of LocalSnapshot instances (`#191 <https://github.com/LeastAuthority/magic-folder/issues/191>`_)
- When told to the daemon will queue and create local snapshots (`#192 <https://github.com/LeastAuthority/magic-folder/issues/192>`_)
-  (`#202 <https://github.com/LeastAuthority/magic-folder/issues/202>`_, `#407 <https://github.com/LeastAuthority/magic-folder/issues/407>`_)
- Added a client endpoint-string to "magic-folder init" and "migrate" (`#251 <https://github.com/LeastAuthority/magic-folder/issues/251>`_)
- Internally, all paths are now text (not bytes) (`#281 <https://github.com/LeastAuthority/magic-folder/issues/281>`_)
- Ensure capabilities cannot leak accidentally in logs (`#559 <https://github.com/LeastAuthority/magic-folder/issues/559>`_)
- Correctly return tahoe-object sizes for delete items (`#606 <https://github.com/LeastAuthority/magic-folder/issues/606>`_)


Dependency/Installation Changes
-------------------------------

- magic-folder supports CentOS 8 (and no longer supports CentOS 7) (`#76 <https://github.com/LeastAuthority/magic-folder/issues/76>`_)
- magic-folder is now compatible with python-cryptography 3.0. (`#208 <https://github.com/LeastAuthority/magic-folder/issues/208>`_)
- magic-folder now has a Python library dependency on Tahoe-LAFS 1.17.0. (`#597 <https://github.com/LeastAuthority/magic-folder/issues/597>`_)


Removed Features
----------------

- The HTTP status API at `/api` has been removed in anticipation of the introduction of a new, better interface. (`#214 <https://github.com/LeastAuthority/magic-folder/issues/214>`_)
- Support for directly synchronizing magic folders stored using the old on-grid schema has been removed. (`#227 <https://github.com/LeastAuthority/magic-folder/issues/227>`_)


Other Changes
-------------

- hot-fix from Tahoe-LAFS repo to do Tahoe-LAFS web api testing (`#142 <https://github.com/LeastAuthority/magic-folder/issues/142>`_)
- Documentation updates. (`#155 <https://github.com/LeastAuthority/magic-folder/issues/155>`_)
- The project now includes basic developer/contributor documentation. (`#164 <https://github.com/LeastAuthority/magic-folder/issues/164>`_)
- The Magic-Folder project has adopted a code of conduct. (`#171 <https://github.com/LeastAuthority/magic-folder/issues/171>`_)
- There is a new database-based configuration design and "magic-folder init" command to use it (`#189 <https://github.com/LeastAuthority/magic-folder/issues/189>`_)
- Tahoe-LAFS 1.15.1 is now required. (`#303 <https://github.com/LeastAuthority/magic-folder/issues/303>`_)
-  (`#305 <https://github.com/LeastAuthority/magic-folder/issues/305>`_, `#311 <https://github.com/LeastAuthority/magic-folder/issues/311>`_, `#314 <https://github.com/LeastAuthority/magic-folder/issues/314>`_, `#315 <https://github.com/LeastAuthority/magic-folder/issues/315>`_)
- Switch to using klein for managing the magic-folder api. (`#362 <https://github.com/LeastAuthority/magic-folder/issues/362>`_)
- Improve handling of serialized eliot messages in tests, and upload eliot logs to circleci. (`#366 <https://github.com/LeastAuthority/magic-folder/issues/366>`_)
- Document /conflicts API and aspects of /status API (`#574 <https://github.com/LeastAuthority/magic-folder/issues/574>`_)


Misc/Other
----------

- `#1 <https://github.com/LeastAuthority/magic-folder/issues/1>`_, `#4 <https://github.com/LeastAuthority/magic-folder/issues/4>`_, `#5 <https://github.com/LeastAuthority/magic-folder/issues/5>`_, `#6 <https://github.com/LeastAuthority/magic-folder/issues/6>`_, `#7 <https://github.com/LeastAuthority/magic-folder/issues/7>`_, `#9 <https://github.com/LeastAuthority/magic-folder/issues/9>`_, `#11 <https://github.com/LeastAuthority/magic-folder/issues/11>`_, `#12 <https://github.com/LeastAuthority/magic-folder/issues/12>`_, `#16 <https://github.com/LeastAuthority/magic-folder/issues/16>`_, `#20 <https://github.com/LeastAuthority/magic-folder/issues/20>`_, `#24 <https://github.com/LeastAuthority/magic-folder/issues/24>`_, `#26 <https://github.com/LeastAuthority/magic-folder/issues/26>`_, `#28 <https://github.com/LeastAuthority/magic-folder/issues/28>`_, `#30 <https://github.com/LeastAuthority/magic-folder/issues/30>`_, `#33 <https://github.com/LeastAuthority/magic-folder/issues/33>`_, `#34 <https://github.com/LeastAuthority/magic-folder/issues/34>`_, `#39 <https://github.com/LeastAuthority/magic-folder/issues/39>`_, `#41 <https://github.com/LeastAuthority/magic-folder/issues/41>`_, `#43 <https://github.com/LeastAuthority/magic-folder/issues/43>`_, `#45 <https://github.com/LeastAuthority/magic-folder/issues/45>`_, `#47 <https://github.com/LeastAuthority/magic-folder/issues/47>`_, `#51 <https://github.com/LeastAuthority/magic-folder/issues/51>`_, `#52 <https://github.com/LeastAuthority/magic-folder/issues/52>`_, `#54 <https://github.com/LeastAuthority/magic-folder/issues/54>`_, `#56 <https://github.com/LeastAuthority/magic-folder/issues/56>`_, `#58 <https://github.com/LeastAuthority/magic-folder/issues/58>`_, `#62 <https://github.com/LeastAuthority/magic-folder/issues/62>`_, `#66 <https://github.com/LeastAuthority/magic-folder/issues/66>`_, `#79 <https://github.com/LeastAuthority/magic-folder/issues/79>`_, `#86 <https://github.com/LeastAuthority/magic-folder/issues/86>`_, `#88 <https://github.com/LeastAuthority/magic-folder/issues/88>`_, `#89 <https://github.com/LeastAuthority/magic-folder/issues/89>`_, `#107 <https://github.com/LeastAuthority/magic-folder/issues/107>`_, `#114 <https://github.com/LeastAuthority/magic-folder/issues/114>`_, `#118 <https://github.com/LeastAuthority/magic-folder/issues/118>`_, `#121 <https://github.com/LeastAuthority/magic-folder/issues/121>`_, `#136 <https://github.com/LeastAuthority/magic-folder/issues/136>`_, `#152 <https://github.com/LeastAuthority/magic-folder/issues/152>`_, `#162 <https://github.com/LeastAuthority/magic-folder/issues/162>`_, `#165 <https://github.com/LeastAuthority/magic-folder/issues/165>`_, `#167 <https://github.com/LeastAuthority/magic-folder/issues/167>`_, `#176 <https://github.com/LeastAuthority/magic-folder/issues/176>`_, `#177 <https://github.com/LeastAuthority/magic-folder/issues/177>`_, `#180 <https://github.com/LeastAuthority/magic-folder/issues/180>`_, `#181 <https://github.com/LeastAuthority/magic-folder/issues/181>`_, `#182 <https://github.com/LeastAuthority/magic-folder/issues/182>`_, `#184 <https://github.com/LeastAuthority/magic-folder/issues/184>`_, `#193 <https://github.com/LeastAuthority/magic-folder/issues/193>`_, `#197 <https://github.com/LeastAuthority/magic-folder/issues/197>`_, `#203 <https://github.com/LeastAuthority/magic-folder/issues/203>`_, `#207 <https://github.com/LeastAuthority/magic-folder/issues/207>`_, `#210 <https://github.com/LeastAuthority/magic-folder/issues/210>`_, `#211 <https://github.com/LeastAuthority/magic-folder/issues/211>`_, `#218 <https://github.com/LeastAuthority/magic-folder/issues/218>`_, `#222 <https://github.com/LeastAuthority/magic-folder/issues/222>`_, `#226 <https://github.com/LeastAuthority/magic-folder/issues/226>`_, `#229 <https://github.com/LeastAuthority/magic-folder/issues/229>`_, `#235 <https://github.com/LeastAuthority/magic-folder/issues/235>`_, `#245 <https://github.com/LeastAuthority/magic-folder/issues/245>`_, `#246 <https://github.com/LeastAuthority/magic-folder/issues/246>`_, `#253 <https://github.com/LeastAuthority/magic-folder/issues/253>`_, `#256 <https://github.com/LeastAuthority/magic-folder/issues/256>`_, `#258 <https://github.com/LeastAuthority/magic-folder/issues/258>`_, `#260 <https://github.com/LeastAuthority/magic-folder/issues/260>`_, `#261 <https://github.com/LeastAuthority/magic-folder/issues/261>`_, `#265 <https://github.com/LeastAuthority/magic-folder/issues/265>`_, `#267 <https://github.com/LeastAuthority/magic-folder/issues/267>`_, `#272 <https://github.com/LeastAuthority/magic-folder/issues/272>`_, `#274 <https://github.com/LeastAuthority/magic-folder/issues/274>`_, `#285 <https://github.com/LeastAuthority/magic-folder/issues/285>`_, `#287 <https://github.com/LeastAuthority/magic-folder/issues/287>`_, `#293 <https://github.com/LeastAuthority/magic-folder/issues/293>`_, `#295 <https://github.com/LeastAuthority/magic-folder/issues/295>`_, `#297 <https://github.com/LeastAuthority/magic-folder/issues/297>`_, `#301 <https://github.com/LeastAuthority/magic-folder/issues/301>`_, `#318 <https://github.com/LeastAuthority/magic-folder/issues/318>`_, `#319 <https://github.com/LeastAuthority/magic-folder/issues/319>`_, `#320 <https://github.com/LeastAuthority/magic-folder/issues/320>`_, `#333 <https://github.com/LeastAuthority/magic-folder/issues/333>`_, `#336 <https://github.com/LeastAuthority/magic-folder/issues/336>`_, `#337 <https://github.com/LeastAuthority/magic-folder/issues/337>`_, `#338 <https://github.com/LeastAuthority/magic-folder/issues/338>`_, `#344 <https://github.com/LeastAuthority/magic-folder/issues/344>`_, `#346 <https://github.com/LeastAuthority/magic-folder/issues/346>`_, `#348 <https://github.com/LeastAuthority/magic-folder/issues/348>`_, `#349 <https://github.com/LeastAuthority/magic-folder/issues/349>`_, `#350 <https://github.com/LeastAuthority/magic-folder/issues/350>`_, `#351 <https://github.com/LeastAuthority/magic-folder/issues/351>`_, `#353 <https://github.com/LeastAuthority/magic-folder/issues/353>`_, `#354 <https://github.com/LeastAuthority/magic-folder/issues/354>`_, `#359 <https://github.com/LeastAuthority/magic-folder/issues/359>`_, `#361 <https://github.com/LeastAuthority/magic-folder/issues/361>`_, `#367 <https://github.com/LeastAuthority/magic-folder/issues/367>`_, `#369 <https://github.com/LeastAuthority/magic-folder/issues/369>`_, `#371 <https://github.com/LeastAuthority/magic-folder/issues/371>`_, `#373 <https://github.com/LeastAuthority/magic-folder/issues/373>`_, `#376 <https://github.com/LeastAuthority/magic-folder/issues/376>`_, `#377 <https://github.com/LeastAuthority/magic-folder/issues/377>`_, `#378 <https://github.com/LeastAuthority/magic-folder/issues/378>`_, `#381 <https://github.com/LeastAuthority/magic-folder/issues/381>`_, `#382 <https://github.com/LeastAuthority/magic-folder/issues/382>`_, `#384 <https://github.com/LeastAuthority/magic-folder/issues/384>`_, `#390 <https://github.com/LeastAuthority/magic-folder/issues/390>`_, `#391 <https://github.com/LeastAuthority/magic-folder/issues/391>`_, `#392 <https://github.com/LeastAuthority/magic-folder/issues/392>`_, `#399 <https://github.com/LeastAuthority/magic-folder/issues/399>`_, `#400 <https://github.com/LeastAuthority/magic-folder/issues/400>`_, `#410 <https://github.com/LeastAuthority/magic-folder/issues/410>`_, `#411 <https://github.com/LeastAuthority/magic-folder/issues/411>`_, `#412 <https://github.com/LeastAuthority/magic-folder/issues/412>`_, `#416 <https://github.com/LeastAuthority/magic-folder/issues/416>`_, `#429 <https://github.com/LeastAuthority/magic-folder/issues/429>`_, `#430 <https://github.com/LeastAuthority/magic-folder/issues/430>`_, `#438 <https://github.com/LeastAuthority/magic-folder/issues/438>`_, `#449 <https://github.com/LeastAuthority/magic-folder/issues/449>`_, `#450 <https://github.com/LeastAuthority/magic-folder/issues/450>`_, `#455 <https://github.com/LeastAuthority/magic-folder/issues/455>`_, `#457 <https://github.com/LeastAuthority/magic-folder/issues/457>`_, `#459 <https://github.com/LeastAuthority/magic-folder/issues/459>`_, `#460 <https://github.com/LeastAuthority/magic-folder/issues/460>`_, `#461 <https://github.com/LeastAuthority/magic-folder/issues/461>`_, `#462 <https://github.com/LeastAuthority/magic-folder/issues/462>`_, `#473 <https://github.com/LeastAuthority/magic-folder/issues/473>`_, `#476 <https://github.com/LeastAuthority/magic-folder/issues/476>`_, `#480 <https://github.com/LeastAuthority/magic-folder/issues/480>`_, `#482 <https://github.com/LeastAuthority/magic-folder/issues/482>`_, `#486 <https://github.com/LeastAuthority/magic-folder/issues/486>`_, `#491 <https://github.com/LeastAuthority/magic-folder/issues/491>`_, `#493 <https://github.com/LeastAuthority/magic-folder/issues/493>`_, `#496 <https://github.com/LeastAuthority/magic-folder/issues/496>`_, `#499 <https://github.com/LeastAuthority/magic-folder/issues/499>`_, `#503 <https://github.com/LeastAuthority/magic-folder/issues/503>`_, `#508 <https://github.com/LeastAuthority/magic-folder/issues/508>`_, `#513 <https://github.com/LeastAuthority/magic-folder/issues/513>`_, `#514 <https://github.com/LeastAuthority/magic-folder/issues/514>`_, `#515 <https://github.com/LeastAuthority/magic-folder/issues/515>`_, `#517 <https://github.com/LeastAuthority/magic-folder/issues/517>`_, `#519 <https://github.com/LeastAuthority/magic-folder/issues/519>`_, `#523 <https://github.com/LeastAuthority/magic-folder/issues/523>`_, `#526 <https://github.com/LeastAuthority/magic-folder/issues/526>`_, `#532 <https://github.com/LeastAuthority/magic-folder/issues/532>`_, `#535 <https://github.com/LeastAuthority/magic-folder/issues/535>`_, `#541 <https://github.com/LeastAuthority/magic-folder/issues/541>`_, `#552 <https://github.com/LeastAuthority/magic-folder/issues/552>`_, `#555 <https://github.com/LeastAuthority/magic-folder/issues/555>`_, `#570 <https://github.com/LeastAuthority/magic-folder/issues/570>`_, `#576 <https://github.com/LeastAuthority/magic-folder/issues/576>`_, `#578 <https://github.com/LeastAuthority/magic-folder/issues/578>`_, `#579 <https://github.com/LeastAuthority/magic-folder/issues/579>`_, `#584 <https://github.com/LeastAuthority/magic-folder/issues/584>`_, `#587 <https://github.com/LeastAuthority/magic-folder/issues/587>`_, `#589 <https://github.com/LeastAuthority/magic-folder/issues/589>`_, `#594 <https://github.com/LeastAuthority/magic-folder/issues/594>`_, `#599 <https://github.com/LeastAuthority/magic-folder/issues/599>`_, `#600 <https://github.com/LeastAuthority/magic-folder/issues/600>`_, `#605 <https://github.com/LeastAuthority/magic-folder/issues/605>`_, `#608 <https://github.com/LeastAuthority/magic-folder/issues/608>`_, `#612 <https://github.com/LeastAuthority/magic-folder/issues/612>`_


