let
  pkgs = import <nixpkgs> {};
in
  pkgs.fetchFromGitHub {
    owner = "tahoe-lafs";
    repo = "tahoe-lafs";
    rev = "tahoe-lafs-1.15.1";
    sha256 = "1kaz21gljxwwldfs8bigyzvqs1h70d66jlj01b6m2bwn98l50m0s";
  }
