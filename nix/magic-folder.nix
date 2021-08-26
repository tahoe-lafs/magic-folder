{ lib, python, buildPythonPackage, pythonPackages, tahoe-lafs, klein, git }:
buildPythonPackage rec {
  pname = "magic-folder";
  version = "0.1.0";
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

  checkPhase = ''
    export MAGIC_FOLDER_HYPOTHESIS_PROFILE="magic-folder-ci"
    ${python}/bin/python -m twisted.trial -j $NIX_BUILD_CORES magic_folder

    # TODO Run the integration test suite.  But pytest_twisted is unpackaged
    # afaict.
  '';
}
