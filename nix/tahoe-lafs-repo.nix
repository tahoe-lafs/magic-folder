let
  pkgs = import <nixpkgs> {};
in
  pkgs.fetchFromGitHub {
    owner = "tahoe-lafs";
    repo = "tahoe-lafs";
    rev = "tahoe-lafs-1.14.0";
    sha256 = "1ahdiapg57g6icv7p2wbzgkwl9lzdlgrsvbm5485414m7z2d6las";
  }
