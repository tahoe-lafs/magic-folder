let
  pkgs = import <nixpkgs> {};
in
  pkgs.fetchFromGitHub {
    owner = "tahoe-lafs";
    repo = "tahoe-lafs";
    rev = "tahoe-lafs-1.14.0";
    sha256 = "02gz83avmd4n0f22ss9hg1xazgikik13z4k7v8ri1ag1vzy0h6bh";
  }
