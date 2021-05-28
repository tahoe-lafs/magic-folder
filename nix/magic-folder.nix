{ lib, python, buildPythonPackage, tahoe-lafs, importlib-metadata, hypothesis, testtools, fixtures, klein, git }:
buildPythonPackage rec {
  pname = "magic-folder";
  version = "2020-02-05";
  src = ../.;

  propagatedBuildInputs = [
    importlib-metadata
    tahoe-lafs
    klein
  ];

  checkInputs = [
    hypothesis
    testtools
    fixtures
  ];

  postPatch = ''
    PATH="$PATH:${git}/bin" ${python}/bin/python setup.py update_version
  '';

  checkPhase = ''
    export MAGIC_FOLDER_HYPOTHESIS_PROFILE="magic-folder-ci"
    ${python}/bin/python -m twisted.trial -j $NIX_BUILD_CORES magic_folder

    # TODO Run the integration test suite.  But pytest_twisted is unpackaged
    # afaict.
  '';
}
