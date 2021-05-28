# This is the main entrypoint for the magic-folder derivation.
{ pkgs ? import <nixpkgs> { } }:
pkgs.python2.pkgs.callPackage ./magic-folder.nix {
  klein = pkgs.python2.pkgs.callPackage ./klein.nix { };
  tahoe-lafs = pkgs.python2.pkgs.callPackage ./tahoe-lafs.nix { };
}
