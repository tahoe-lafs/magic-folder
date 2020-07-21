# This is the main entrypoint for the Tahoe-LAFS derivation.
{ pkgs ? import <nixpkgs> { } }:
pkgs.python2.pkgs.callPackage ./magic-folder.nix {
  tahoe-lafs = pkgs.python2.pkgs.callPackage ./tahoe-lafs.nix { };
}
