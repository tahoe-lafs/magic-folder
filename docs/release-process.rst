Magic-Folder Release Process
============================

There is no schedule for magic-folders releases.
We endeavour to keep ``main`` as always releasable.


Versioning Scheme
-----------------

We use a kind of Calendar Versioning (`https://calver.org/`_):
`YY.MM.NN` where these values are:

* `YY`: the last two digits of the current year;
* `MM`: the two-digit month;
* `NN`: a number that starts at 0 and increases for every release in a given month.


Updating the Version
--------------------

The version is stored (and therefore must be updated) in these places:

* src/magic_folder/__init__.py
* ...


Release Checklist
-----------------

FIXME TODO: flesh out these steps (this is currently an outline)

* Ensure we're locally up-to-date and on the `main` branch:
  - git checkout main
  - git pull

* Update the version

* Update the NEWS file:
  - run `towncrier`
  - ..

* Update the `releases.rst` file with the new release information

* Commit the above changes

* Sign a Git tag for the release:
  - git tag -s -u meejah@meejah.ca -m "release Magic Folders 22.1.0" v22.1.0

* Produce the "wheel" file (ends up in dist/)

* Test that the release works:
  - install the wheel in a fresh venv
  - run `magic-folder`
  - run `magic-folder-api`

* Sign the "wheel" file, producing a `.asc` file

* Use `twine` to upload both the wheel and signature file to PyPI

* Upload the wheel and signature file to GitHub

