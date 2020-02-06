{ python2Packages }:
python2Packages.callPackage ((import ./tahoe-lafs-repo.nix) + "/nix") { }
