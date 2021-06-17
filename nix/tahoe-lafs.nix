{ python2Packages }:
let
  repo = ((import ./tahoe-lafs-repo.nix) + "/nix");
  tahoe-lafs-drv = repo + "/tahoe-lafs.nix";
  tahoe-lafs = python2Packages.callPackage tahoe-lafs-drv { };
  versioned-tahoe-lafs = tahoe-lafs.overrideAttrs (old: rec {
    # Upstream is versioned as 1.14.0.dev, still, even though it is now
    # 1.15.1.
    version = "1.15.1";
    name = "tahoe-lafs-1.15.1";
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
