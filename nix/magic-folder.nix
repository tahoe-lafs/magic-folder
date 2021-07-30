{ lib, python, buildPythonPackage, pythonPackages, tahoe-lafs, klein, git }:
buildPythonPackage rec {
  pname = "magic-folder";
  version = "2020-07-30";
  src = lib.cleanSource ../.;

  propagatedBuildInputs = with pythonPackages; [
    configparser
    tahoe-lafs
    klein
  ];

  checkInputs = with pythonPackages; [
    hypothesis
    testtools
    fixtures
  ];

  postPatch = ''
    # Generate _version.py ourselves since we can't rely on the Python code
    # extracting the information from the .git directory that is likely not
    # available.
    cat > src/magic_folder/_version.py <<EOF

# This _version.py is generated from metadata by nix/magic-folder.nix.

__pkgname__ = "magic-folder"
real_version = "${version}"
full_version = "${version}"
branch = "main"
verstr = "${version}"
__version__ = verstr
EOF
  '';

  checkPhase = ''
    export MAGIC_FOLDER_HYPOTHESIS_PROFILE="magic-folder-ci"
    ${python}/bin/python -m twisted.trial -j $NIX_BUILD_CORES magic_folder

    # TODO Run the integration test suite.  But pytest_twisted is unpackaged
    # afaict.
  '';
}
