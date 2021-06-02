{ lib, buildPythonPackage, fetchPypi, pythonPackages }:

buildPythonPackage rec {
  pname = "Tubes";
  version = "0.2.0";

  propagatedBuildInputs = with pythonPackages; [
    six
    characteristic
    twisted
  ];

  doCheck = false;

  src = fetchPypi {
    inherit pname version;
    sha256 = "sha256:0sg1gg2002h1xsgxigznr1zk1skwmhss72dzk6iysb9k9kdgymcd";
  };
}
