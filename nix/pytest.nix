{ lib, buildPythonPackage, fetchPypi, attrs, py
, setuptools_scm, setuptools, six, pluggy, funcsigs, more-itertools
, atomicwrites, writeText, pathlib2, wcwidth, packaging
}:
buildPythonPackage rec {
  version = "4.6.11";
  pname = "pytest";

  src = fetchPypi {
    inherit pname version;
    sha256 = "50fa82392f2120cc3ec2ca0a75ee615be4c479e66669789771f1758332be4353";
  };

  buildInputs = [ setuptools_scm ];
  propagatedBuildInputs = [ attrs py setuptools six pluggy more-itertools atomicwrites wcwidth packaging funcsigs pathlib2 ];

  # To prevent infinite recursion with hypothesis
  doCheck = false;

  # Remove .pytest_cache when using py.test in a Nix build
  setupHook = writeText "pytest-hook" ''
    pytestcachePhase() {
        find $out -name .pytest_cache -type d -exec rm -rf {} +
    }
    preDistPhases+=" pytestcachePhase"
  '';
}
