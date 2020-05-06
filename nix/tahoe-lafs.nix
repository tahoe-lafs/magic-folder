{ python2Packages }:
let
  # Manually assemble the tahoe-lafs build inputs because tahoe-lafs 1.14.0
  # eliot package runs the eliot test suite which is flaky.  Doing this gives
  # us a place to insert a `doCheck = false` (at the cost of essentially
  # duplicating tahoe-lafs' default.nix).  Not ideal but at least we can throw
  # it away when we upgrade to the next tahoe-lafs version.
  repo = ((import ./tahoe-lafs-repo.nix) + "/nix");
  nevow-drv = repo + "/nevow.nix";
  nevow = python2Packages.callPackage nevow-drv { };
  eliot-drv = repo + "/eliot.nix";
  eliot = (python2Packages.callPackage eliot-drv { }).overrideAttrs (old: {
    doCheck = false;
  });
  tahoe-lafs-drv = repo + "/tahoe-lafs.nix";
  tahoe-lafs = python2Packages.callPackage tahoe-lafs-drv {
    inherit nevow eliot;
  };
in
  tahoe-lafs
