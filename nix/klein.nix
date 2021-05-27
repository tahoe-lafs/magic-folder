{ lib, buildPythonPackage, fetchPypi }:

buildPythonPackage rec {
  pname = "klein";
  version = "20.6.0";

  src = fetchPypi {
    inherit pname version;
    sha256 = "6584b9cdff4959b9dcee95a1c1c20010f521a2a12c4ff3cdd8b903a9b0e993f6";
  };
}
