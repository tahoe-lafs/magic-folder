# This is the main entrypoint for the magic-folder derivation.
{ pkgs ? import <nixpkgs> { overlays = [ (import ./overlay.nix) ]; } }:
pkgs.python2.pkgs.callPackage ./magic-folder.nix {
  tahoe-lafs = pkgs.python2.pkgs.callPackage ./tahoe-lafs.nix { };
}
