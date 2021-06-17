{ lib, buildPythonPackage, fetchPypi }:

buildPythonPackage rec {
  pname = "attrs";
  version = "19.3.0";

  src = fetchPypi {
    inherit pname version;
    sha256 = "f7b7ce16570fe9965acd6d30101a28f62fb4a7f9e926b3bbc9b61f8b04247e72";
  };

  # To prevent infinite recursion with pytest
  doCheck = false;
}
