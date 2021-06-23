# This is the main entrypoint for the magic-folder derivation.
{ pkgs ? import <nixpkgs> { } }:
let
  tahoe-repo = (import ./tahoe-lafs-repo.nix);
  pkgs' = pkgs.appendOverlays [
    (import (tahoe-repo + "/nix/overlays.nix"))
    (import ./overlays.nix)
  ];
  callPackage = pkgs'.python27Packages.callPackage;
in
  callPackage ./magic-folder.nix {
    # klein is not packaged in nixos 19.09 so supply it here
    klein = callPackage ./klein.nix {
      # tubes is not packaged either, though other klein dependencies are
      tubes = callPackage ./tubes.nix { };
    };

    # The packaged tahoe-lafs is an "application" rather than a library.  It's
    # possible it would work if we could mangle it into a library but instead
    # we'll just supply our own package based on the upstream Tahoe-LAFS nix
    # expressions (which provide a library directly).
    tahoe-lafs = callPackage ./tahoe-lafs.nix { };
  }
