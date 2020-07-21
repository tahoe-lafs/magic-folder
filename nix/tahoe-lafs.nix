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
    doInstallCheck = false;
  });
  tahoe-lafs-drv = repo + "/tahoe-lafs.nix";
  tahoe-lafs = python2Packages.callPackage tahoe-lafs-drv {
    inherit nevow eliot;
  };
  versioned-tahoe-lafs = tahoe-lafs.overrideAttrs (old: rec {
    # Upstream is versioned as 1.14.0.dev which is the same as 1.14.0.dev0
    # which is a "smaller" version than 1.14.0!  The reality is that we've
    # pinned exactly 1.14.0 so just make that the version.
    version = "1.14.0";
    name = "tahoe-lafs-1.14.0";
    postPatch = ''
      ${old.postPatch}

      # We got rid of our .git directory so the built-in version computing logic
      # won't work.  The exact strings we emit here matter because of custom
      # parsing Tahoe-LAFS applies.
      echo 'verstr = "${version}"' > src/allmydata/_version.py
      echo '__version__ = verstr' >> src/allmydata/_version.py
    '';
  });
in
  versioned-tahoe-lafs
