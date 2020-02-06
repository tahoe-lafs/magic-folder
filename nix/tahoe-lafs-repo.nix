let
  pkgs = import <nixpkgs> {};
in
  pkgs.fetchFromGitHub {
    owner = "tahoe-lafs";
    repo = "tahoe-lafs";
    rev = "5bd84895fdc436feb0753e824044a016430896da";
    sha256 = "1ygzf00r7230v5ma02cs9n0fxgd2kq9iyrp4r8gxayw3cgbbhnlm";
  }
